from __future__ import annotations
from flask import Blueprint, jsonify, request
from models import Conversation, ConversationState

bp = Blueprint("viewer_selection", __name__)

@bp.get("/v1/chats/<chat_id>/viewer/selection")
def get_selection(chat_id: str):
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404
    row = ConversationState.get_or_create(chat_id)
    sel = dict(row.state_json.get("viewer_selection") or {})
    # Default → neueste Model-Version
    if not sel:
        sel = {"mode": "model", "project_id": getattr(conv, "vectoplan_project_id", None),
               "model_id": getattr(conv, "vectoplan_model_id", None), "version_id": None}
    return jsonify(sel), 200

@bp.put("/v1/chats/<chat_id>/viewer/selection")
def put_selection(chat_id: str):
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "model")
    pid  = data.get("project_id")
    mid  = data.get("model_id")
    vid  = data.get("version_id")  # None = neueste Version
    patch = {"viewer_selection": {"mode": mode, "project_id": pid, "model_id": mid, "version_id": vid}}
    ConversationState.merge_patch(chat_id, patch)
    return jsonify({"ok": True}), 200
