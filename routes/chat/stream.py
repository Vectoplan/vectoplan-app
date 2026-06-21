# services/app/routes/chat/stream.py
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from flask import Response, current_app, jsonify, request, stream_with_context

from extensions import db
from models import Blob, Conversation

from . import bp
from .helpers import (
    apply_chatai_actions,
    attachment_meta_for_transcript,
    call_chatai,
    chunk_text,
    load_attachments,
    numeric_project_id,
    resolve_viewer_url,
    sse,
)


# ───────────────────────── Constants ─────────────────────────

LEGACY_PUBLISH_INTENTS = {
    "UPLOAD_VERSION",
    "PUBLISH_3D",
    "PUBLISH_MODEL",
    "UPLOAD_MODEL",
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

    response = jsonify(payload)
    response.status_code = status
    return _apply_json_headers(response)


def _sse_headers() -> Dict[str, str]:
    return {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }


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


def _normalize_file_ids(raw_attachments: Any) -> List[str]:
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

    deduped: List[str] = []
    seen = set()

    for value in out:
        if value in seen:
            continue

        seen.add(value)
        deduped.append(value)

    return deduped


def _include_attachments(data: Dict[str, Any]) -> bool:
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
    Detect legacy publish intents without executing them.
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

def _get_conversation(chat_id: str) -> Optional[Conversation]:
    try:
        return Conversation.query.get(str(chat_id))
    except Exception:
        try:
            current_app.logger.exception("conversation lookup failed")
        except Exception:
            pass
        return None


def _get_or_create_conversation(data: Dict[str, Any]) -> Tuple[Optional[Conversation], Optional[Tuple[str, int, str]]]:
    """
    Return (conversation, error).

    Error tuple format:
      (message, status, code)
    """
    try:
        chat_id = str(data.get("chat_id") or "").strip()

        if chat_id:
            conv = _get_conversation(chat_id)

            if not conv:
                return None, ("chat not found", 404, "chat_not_found")

            return conv, None

        conv = Conversation()
        db.session.add(conv)
        db.session.flush()

        return conv, None

    except Exception as exc:
        current_app.logger.exception("conversation create/load failed")
        return None, (str(exc), 500, "conversation_failed")


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


def _commit_or_raise() -> None:
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


# ───────────────────────── Route ─────────────────────────

@bp.post("/v1/chat/stream")
def chat_stream():
    """
    Streaming Chat API.

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

    Events:
    - start
    - actions
    - delta
    - done
    - error

    Speckle-free behavior:
    - no upload event
    - no background thread
    - no legacy 3D publish
    - done.viewer_url points to the local editor iframe route for compatibility
    """
    data = _request_json()
    text = str(data.get("message") or "")

    raw_attachments = data.get("attachments") or []
    file_ids = _normalize_file_ids(raw_attachments)

    try:
        current_app.logger.info(
            "chat(stream): raw_attachments=%s -> file_ids=%s",
            raw_attachments,
            file_ids,
        )
    except Exception:
        pass

    if not text and not file_ids:
        return _json_error("empty message", 400, code="empty_message")

    include_attachments = _include_attachments(data)

    # Accepted for backward compatibility, but intentionally ignored.
    defer_upload_requested = (
        _as_bool(request.args.get("defer_upload"))
        or _as_bool(data.get("defer_upload"))
    )

    try:
        conv, error = _get_or_create_conversation(data)

        if error:
            message, status, code = error
            return _json_error(message, status, code=code)

        if conv is None:
            return _json_error(
                "conversation unavailable",
                500,
                code="conversation_unavailable",
            )

        attachments_meta = _load_attachment_meta(file_ids)

        user_msg = _append_user_message(
            conv,
            text=text,
            attachments=attachments_meta,
        )

        _commit_or_raise()

        payload = {
            "chat_id": conv.id,
            "message": text,
            "coordinate": [0.0, 0.0],
            "project_id": numeric_project_id(conv),
            "chat_message_id": _chat_message_id(),
            "attachments": load_attachments(file_ids) if (include_attachments and file_ids) else [],
        }

        conv_id = str(conv.id)
        user_msg_id = user_msg.get("id") if isinstance(user_msg, dict) else None
        app = current_app._get_current_object()

        def generate():
            assistant_msg: Dict[str, Any] = {}
            full_reply = ""
            out_json: Optional[Dict[str, Any]] = None
            actions_result: Dict[str, Any] = {}
            editor_url = ""

            with app.app_context():
                try:
                    yield sse(
                        {
                            "event": "start",
                            "chat_id": conv_id,
                            "legacy_3d_backend": False,
                        }
                    )

                    try:
                        out_json, full_reply = call_chatai(payload)
                    except Exception as exc:
                        app.logger.warning(
                            "ChatAI call failed (stream): %s",
                            exc,
                            exc_info=True,
                        )
                        out_json = None
                        full_reply = f"Fehler in ChatAI: {exc}"

                    legacy_publish_requested, intent = _legacy_publish_intent(out_json)

                    try:
                        app.logger.info(
                            (
                                "chat(stream): intent=%s "
                                "legacy_publish_requested=%s "
                                "defer_upload_ignored=%s files=%d"
                            ),
                            intent,
                            legacy_publish_requested,
                            defer_upload_requested,
                            len(file_ids),
                        )
                    except Exception:
                        pass

                    if legacy_publish_requested:
                        yield sse(
                            {
                                "event": "publish",
                                "requested": True,
                                "intent": intent,
                                "executed": False,
                                "reason": "legacy_publish_disabled",
                                "defer_upload_ignored": bool(defer_upload_requested),
                            }
                        )

                    conv_for_actions = _get_conversation(conv_id)

                    if conv_for_actions and isinstance(out_json, dict):
                        try:
                            actions_result = apply_chatai_actions(conv_for_actions, out_json)
                            yield sse(
                                {
                                    "event": "actions",
                                    "result": actions_result,
                                }
                            )
                        except Exception as exc:
                            app.logger.warning(
                                "apply actions failed (stream): %s",
                                exc,
                                exc_info=True,
                            )

                    accumulated = ""

                    try:
                        for piece in chunk_text(full_reply or ""):
                            accumulated += piece
                            yield sse(
                                {
                                    "event": "delta",
                                    "text": piece,
                                }
                            )
                    except Exception:
                        accumulated = str(full_reply or "")
                        if accumulated:
                            yield sse(
                                {
                                    "event": "delta",
                                    "text": accumulated,
                                }
                            )

                    final_conv = _get_conversation(conv_id)

                    if final_conv:
                        try:
                            assistant_msg = _append_assistant_message(
                                final_conv,
                                text=accumulated,
                            )
                            db.session.add(final_conv)
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                            app.logger.warning(
                                "assistant message commit failed (stream)",
                                exc_info=True,
                            )

                        try:
                            editor_url = resolve_viewer_url(final_conv)
                        except Exception:
                            editor_url = ""

                    yield sse(
                        {
                            "event": "done",
                            "chat_id": conv_id,
                            "user_message_id": user_msg_id,
                            "assistant_msg": assistant_msg,
                            "viewer_url": editor_url,
                            "editor_url": editor_url,
                            "actions": actions_result,
                            "publish": {
                                "requested": bool(legacy_publish_requested),
                                "intent": intent or None,
                                "executed": False,
                                "reason": (
                                    "legacy_publish_disabled"
                                    if legacy_publish_requested
                                    else None
                                ),
                                "defer_upload_ignored": bool(defer_upload_requested),
                            },
                            "legacy_3d_backend": False,
                        }
                    )

                except GeneratorExit:
                    raise

                except Exception as exc:
                    try:
                        app.logger.exception("chat stream generator failed")
                    except Exception:
                        pass

                    yield sse(
                        {
                            "event": "error",
                            "error": str(exc),
                            "code": "stream_failed",
                        }
                    )

        return Response(
            stream_with_context(generate()),
            headers=_sse_headers(),
        )

    except Exception as exc:
        current_app.logger.exception("chat_stream failed")
        return _json_error(str(exc), 500, code="chat_stream_failed")