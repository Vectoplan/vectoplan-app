# /services/app/routes/state.py
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Dict, Tuple, Optional, List

from flask import Blueprint, jsonify, request, current_app, Response
from werkzeug.exceptions import BadRequest

from extensions import db  # noqa: F401
from models import Conversation
import messages as msg  # uses get_state / merge_state

bp = Blueprint("state_api", __name__)


# ───────────────────────── helpers ─────────────────────────

def _json_error(message: str, code: int = 400):
    try:
        return jsonify({"error": message}), code
    except Exception:
        return {"error": message}, code


def _cfg_int(key: str, default: int) -> int:
    try:
        v = int(current_app.config.get(key, default))
        return v if v >= 0 else default
    except Exception:
        return default


def _as_bool(x: Any, default: bool = False) -> bool:
    try:
        if isinstance(x, bool):
            return x
        if isinstance(x, (int, float)):
            return bool(x)
        if isinstance(x, str):
            v = x.strip().lower()
            if v in {"1", "true", "yes", "y", "on"}:
                return True
            if v in {"0", "false", "no", "n", "off"}:
                return False
        return default
    except Exception:
        return default


def _safe_json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj or {}, sort_keys=True, separators=(",", ":"))
    except Exception:
        try:
            return json.dumps({})
        except Exception:
            return "{}"


def _etag_for(state: Dict[str, Any]) -> str:
    try:
        raw = _safe_json_dumps(state).encode("utf-8", "ignore")
        return sha256(raw).hexdigest()[:16]
    except Exception:
        return ""


def _get_cs_model():
    try:
        from models import ConversationState  # type: ignore
        return ConversationState
    except Exception:
        return None


def _db_get_state(chat_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Returns (state_json, updated_at_iso) or (None, None) on failure."""
    CS = _get_cs_model()
    if CS is None:
        return None, None
    try:
        row = CS.query.filter_by(conversation_id=chat_id).one_or_none()
        if not row:
            return {}, None
        state = dict(row.state_json or {})
        ts = None
        try:
            ts = (row.updated_at).isoformat() + "Z" if getattr(row, "updated_at", None) else None
        except Exception:
            ts = None
        return state, ts
    except Exception as ex:
        try:
            current_app.logger.warning("state: DB get failed (%s)", ex, exc_info=True)
        except Exception:
            pass
        return None, None


def _db_replace_state(chat_id: str, new_state: Dict[str, Any]) -> bool:
    CS = _get_cs_model()
    if CS is None:
        return False
    try:
        row = CS.query.filter_by(conversation_id=chat_id).one_or_none()
        if row is None:
            row = CS(conversation_id=chat_id, state_json=dict(new_state or {}))
            db.session.add(row)
        else:
            row.state_json = dict(new_state or {})
        db.session.commit()
        return True
    except Exception as ex:
        db.session.rollback()
        try:
            current_app.logger.warning("state: DB replace failed (%s)", ex, exc_info=True)
        except Exception:
            pass
        return False


def _db_clear_state(chat_id: str) -> bool:
    return _db_replace_state(chat_id, {})


def _body_size_guard(raw: Any) -> bool:
    """
    True if body within limit. Limit is STATE_MAX_BYTES (default 1MB).
    """
    try:
        limit = _cfg_int("STATE_MAX_BYTES", 1_000_000)
        if limit <= 0:
            return True
        try:
            # Prefer raw content_length if available
            clen = int(request.content_length or 0)
            if clen and clen > 0:
                return clen <= limit
        except Exception:
            pass
        # Fallback: compute serialized size
        b = _safe_json_dumps(raw).encode("utf-8", "ignore")
        return len(b) <= limit
    except Exception:
        return True


def _precondition_check(if_match: Optional[str], current_etag: str) -> Optional[Response]:
    """
    If If-Match header present and mismatched → return 412 response.
    """
    try:
        if not if_match:
            return None
        # strip quotes if given
        token = if_match.strip().strip('"')
        if token and token != current_etag:
            resp = jsonify({"error": "precondition failed", "expected": token, "current": current_etag})
            return (resp, 412)
        return None
    except Exception:
        return None


# ───────────────────────── endpoints ─────────────────────────

@bp.get("/v1/chats/<chat_id>/state")
def get_state(chat_id: str):
    try:
        conv = Conversation.query.get(chat_id)
        if not conv:
            return _json_error("chat not found", 404)

        # Prefer DB model if available
        state, updated_at = _db_get_state(chat_id)
        if state is None:
            state = msg.get_state(chat_id)
            updated_at = None

        etag = _etag_for(state)
        incoming = request.headers.get("If-None-Match")
        if etag and incoming and incoming.strip('"') == etag:
            resp = Response(status=304)
            try:
                resp.set_etag(etag)
            except Exception:
                pass
            return resp

        out = {"chat_id": conv.id, "state": state, "updated_at": updated_at, "etag": etag}
        resp = jsonify(out)
        try:
            if etag:
                resp.set_etag(etag)
            resp.headers.setdefault("Cache-Control", "no-store")
        except Exception:
            pass
        return resp, 200
    except Exception as ex:
        try:
            current_app.logger.exception("get_state failed")
        except Exception:
            pass
        return _json_error(str(ex), 500)


@bp.head("/v1/chats/<chat_id>/state")
def head_state(chat_id: str):
    """Lightweight ETag probe."""
    try:
        conv = Conversation.query.get(chat_id)
        if not conv:
            return _json_error("chat not found", 404)

        state, _ = _db_get_state(chat_id)
        if state is None:
            state = msg.get_state(chat_id)

        etag = _etag_for(state or {})
        resp = Response(status=200)
        try:
            if etag:
                resp.set_etag(etag)
            resp.headers.setdefault("Cache-Control", "no-store")
        except Exception:
            pass
        return resp
    except Exception as ex:
        try:
            current_app.logger.exception("head_state failed")
        except Exception:
            pass
        return _json_error(str(ex), 500)


@bp.put("/v1/chats/<chat_id>/state")
def put_state(chat_id: str):
    """
    Replace or merge state.
    Body options:
      • { "state": { ... }, "replace": true }        # full replace (DB path preferred)
      • { "patch": { ... } }                          # merge
      • { ... }                                       # merge (top-level object)
    Query param ?replace=1 overrides body flag.
    Supports optimistic concurrency with If-Match: "<etag>".
    """
    try:
        conv = Conversation.query.get(chat_id)
        if not conv:
            return _json_error("chat not found", 404)

        data = request.get_json(silent=True)
        if data is None:
            return _json_error("json body required", 400)
        if not isinstance(data, dict):
            return _json_error("body must be object", 400)

        if not _body_size_guard(data):
            return _json_error("payload too large", 413)

        # current ETag for If-Match
        try:
            current_state, _ = _db_get_state(chat_id)
            if current_state is None:
                current_state = msg.get_state(chat_id)
            current_etag = _etag_for(current_state or {})
        except Exception:
            current_etag = ""

        pre = _precondition_check(request.headers.get("If-Match"), current_etag)
        if pre:
            return pre

        replace_flag = _as_bool(request.args.get("replace"), False) or _as_bool(data.get("replace"), False)

        if replace_flag:
            new_state = data.get("state")
            if not isinstance(new_state, dict):
                return _json_error("state object required for replace", 400)

            ok = _db_replace_state(chat_id, new_state)
            if ok:
                etag = _etag_for(new_state)
                out = {"status": "ok", "chat_id": conv.id, "state": new_state, "etag": etag}
                resp = jsonify(out)
                try:
                    if etag:
                        resp.set_etag(etag)
                except Exception:
                    pass
                return resp, 200

            # Fallback: merge-only via messages
            try:
                current_app.logger.warning("state: replace degraded to merge fallback (no DB or error)")
            except Exception:
                pass
            merged = msg.merge_state(chat_id, new_state)
            etag = _etag_for(merged)
            return jsonify({
                "status": "degraded-merge",
                "warning": "exact replace unavailable; performed merge",
                "chat_id": conv.id,
                "state": merged,
                "etag": etag,
            }), 200

        # merge mode
        patch = data.get("patch")
        if patch is None:
            patch = {k: v for k, v in data.items() if k not in {"replace"}}
        if not isinstance(patch, dict):
            return _json_error("patch must be object", 400)

        merged = msg.merge_state(chat_id, patch)
        etag = _etag_for(merged)
        out = {"status": "ok", "chat_id": conv.id, "state": merged, "etag": etag}
        resp = jsonify(out)
        try:
            if etag:
                resp.set_etag(etag)
        except Exception:
            pass
        return resp, 200

    except BadRequest as br:
        return _json_error(str(br), 400)
    except Exception as ex:
        try:
            current_app.logger.exception("put_state failed")
        except Exception:
            pass
        return _json_error(str(ex), 500)


@bp.patch("/v1/chats/<chat_id>/state/clear")
def clear_state(chat_id: str):
    """
    Clears conversation state.
    DB path performs true clear. Fallback path attempts best-effort.
    """
    try:
        conv = Conversation.query.get(chat_id)
        if not conv:
            return _json_error("chat not found", 404)

        if _db_clear_state(chat_id):
            return jsonify({"status": "ok", "chat_id": conv.id, "state": {}}), 200

        # Fallback: cannot truly clear without DB; store marker
        best = msg.merge_state(chat_id, {"__cleared_at__": (request.headers.get("X-Time") or "")})
        return jsonify({
            "status": "degraded",
            "warning": "exact clear unavailable; merge applied",
            "chat_id": conv.id,
            "state": best,
        }), 200
    except Exception as ex:
        try:
            current_app.logger.exception("clear_state failed")
        except Exception:
            pass
        return _json_error(str(ex), 500)
