# /services/app/routes/templates.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request, current_app
from werkzeug.exceptions import BadRequest

from extensions import db  # noqa: F401  # evtl. für DB-gestützte Templates notwendig
from models import Conversation  # noqa: F401  # nur für Typhinweise
import messages as msg  # messages.py

bp = Blueprint("templates_api", __name__)


# ───────────────────────── helpers ─────────────────────────

def _json_error(message: str, code: int = 400):
    try:
        return jsonify({"error": message}), code
    except Exception:
        return {"error": message}, code


def _as_bool(x: Any, default: bool = False) -> bool:
    try:
        if isinstance(x, bool):
            return x
        if isinstance(x, str):
            v = x.strip().lower()
            if v in {"1", "true", "yes", "y", "on"}:
                return True
            if v in {"0", "false", "no", "n", "off"}:
                return False
        if isinstance(x, (int, float)):
            return bool(x)
        return default
    except Exception:
        return default


def _sanitize_role(v: Any, fallback: str = "service") -> str:
    try:
        s = str(v or fallback).strip().lower()
        return s if s in {"service", "assistant", "system"} else fallback
    except Exception:
        return fallback


def _sanitize_trace(v: Any) -> List[str]:
    try:
        if not isinstance(v, list):
            return []
        out: List[str] = []
        for x in v:
            try:
                s = str(x).strip()
                if s:
                    out.append(s)
            except Exception:
                continue
        return out[:10]
    except Exception:
        return []


# ───────────────────────── templates registry ─────────────────────────

@bp.get("/v1/templates")
def list_templates():
    """
    Optional query params:
      - active=1|0   → Filter nach Aktiv-Flag
      - q=...        → Substring-Suche in key/title/renderer (clientseitig)
    """
    try:
        items = msg.list_templates() or []

        # active-Filter
        active_q = request.args.get("active")
        if active_q is not None:
            want_active = _as_bool(active_q, True)
            items = [t for t in items if bool(t.get("is_active", True)) == want_active]

        # einfache clientseitige Suche
        q = (request.args.get("q") or "").strip().lower()
        if q:
            def _match(t: Dict[str, Any]) -> bool:
                try:
                    return any(q in str(t.get(k, "")).lower() for k in ("key", "title", "renderer"))
                except Exception:
                    return False
            items = [t for t in items if _match(t)]

        return jsonify({"items": items, "total": len(items)}), 200
    except Exception as ex:
        try:
            current_app.logger.exception("list_templates failed")
        except Exception:
            pass
        return _json_error(str(ex), 500)


@bp.get("/v1/templates/<key>")
def get_template(key: str):
    try:
        t = msg.get_template(key)
        if not t:
            return _json_error("not found", 404)
        return jsonify(t), 200
    except Exception as ex:
        try:
            current_app.logger.exception("get_template failed")
        except Exception:
            pass
        return _json_error(str(ex), 500)


@bp.post("/v1/templates")
def upsert_template():
    """
    Body JSON:
      { key, renderer?, title?, version?, schema_json?, is_active? }
    Upsert, validiert grob den Typ der Felder.
    """
    try:
        data = request.get_json(silent=True) or {}
        key = str(data.get("key") or "").strip()
        if not key:
            return _json_error("key required", 400)

        schema_json = data.get("schema_json") or data.get("schema") or {}
        if schema_json and not isinstance(schema_json, dict):
            return _json_error("schema_json must be object", 400)

        row = msg.register_template(
            key=key,
            schema_json=schema_json if isinstance(schema_json, dict) else {},
            renderer=str(data.get("renderer") or "InfoCard"),
            title=(data.get("title") or key),
            version=int(data.get("version") or 1),
            is_active=_as_bool(data.get("is_active"), True),
        )
        return jsonify(row), 200
    except BadRequest as br:
        return _json_error(str(br), 400)
    except Exception as ex:
        try:
            current_app.logger.exception("upsert_template failed")
        except Exception:
            pass
        return _json_error(str(ex), 500)


@bp.post("/v1/templates/seed")
def seed_templates():
    """
    Lädt Seeds aus Config (TEMPLATE_SEED oder TEMPLATE_SEED_PATH).
    Überschreibt existierende Keys nicht.
    """
    try:
        # msg._ensure_seed_loaded() ist idempotent
        try:
            setattr(msg._ensure_seed_loaded, "_done", False)  # erneutes Laden erlauben
        except Exception:
            pass
        msg._ensure_seed_loaded()
        items = msg.list_templates() or []
        return jsonify({"status": "ok", "total": len(items), "items": items}), 200
    except Exception as ex:
        try:
            current_app.logger.exception("seed_templates failed")
        except Exception:
            pass
        return _json_error(str(ex), 500)


# ───────────────────────── post card/text to transcript ─────────────────────────

@bp.post("/v1/chats/<chat_id>/messages")
def post_chat_message(chat_id: str):
    """
    Postet strukturierte Karten oder Text in das Transcript.
    Body JSON Varianten:
      A) { "template_key":"project_welcome", "payload":{...}, "role":"service", "trace":["ChatAI"], "validate":true }
      B) { "text":"...", "role":"assistant", "trace":[...] }

    Antwort: { status, chat_id, message, error? }
    """
    try:
        conv = Conversation.query.get(chat_id)
        if not conv:
            return _json_error("chat not found", 404)

        data = request.get_json(silent=True) or {}
        template_key = str(data.get("template_key") or "").strip()
        text = data.get("text")

        if not template_key and not isinstance(text, str):
            return _json_error("template_key or text required", 400)

        role = _sanitize_role(data.get("role") or ("assistant" if isinstance(text, str) else "service"))
        trace = _sanitize_trace(data.get("trace"))
        validate = _as_bool(data.get("validate"), True)

        if template_key:
            payload = data.get("payload") or {}
            if payload and not isinstance(payload, dict):
                return _json_error("payload must be object", 400)

            res = msg.post_card_message(
                conversation=conv,
                template_key=template_key,
                payload=payload if isinstance(payload, dict) else {},
                role=role,
                trace=trace,
                validate=validate,
            )
            ok = bool(res.get("ok"))
            return jsonify({
                "status": "ok" if ok else "invalid",
                "chat_id": conv.id,
                "message": res.get("message"),
                "error": res.get("error"),
            }), (200 if ok else 422)

        # plain text
        res2 = msg.post_text_message(
            conversation=conv,
            text=str(text or ""),
            role=role,
            trace=trace,
            attachments=None,
            meta=None,
        )
        return jsonify({
            "status": "ok" if res2.get("ok") else "error",
            "chat_id": conv.id,
            "message": res2.get("message"),
        }), (200 if res2.get("ok") else 500)

    except Exception as ex:
        try:
            current_app.logger.exception("post_chat_message failed")
        except Exception:
            pass
        return _json_error(str(ex), 500)
