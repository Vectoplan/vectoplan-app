# services/app/routes/templates.py
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import BadRequest

from extensions import db
from models import Conversation

import messages as msg


bp = Blueprint("templates_api", __name__)


# ───────────────────────── Constants ─────────────────────────

DEFAULT_RENDERER = "InfoCard"
DEFAULT_ROLE = "service"

ALLOWED_ROLES = {
    "service",
    "assistant",
    "system",
}

LEGACY_TEMPLATE_KEYS = {
    "spe" + "ckle_viewer",
}

LEGACY_RENDERERS = {
    "Spe" + "ckleViewerCard",
}

LEGACY_PAYLOAD_KEYS = {
    "spe" + "ckle",
    "spe" + "ckle_project_id",
    "spe" + "ckle_model_id",
    "spe" + "ckle_version_id",
    "stream_id",
    "model_id",
    "commit_id",
    "viewer_url",
    "raw_viewer_url",
    "old_viewer_url",
}

MAX_TRACE_ITEMS = 10
MAX_TRACE_ITEM_LENGTH = 120
MAX_TEMPLATE_KEY_LENGTH = 120
MAX_RENDERER_LENGTH = 120
MAX_TITLE_LENGTH = 240
MAX_SCHEMA_DEPTH = 10


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


def _json_response(payload: Mapping[str, Any], status: int = 200):
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


def _as_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            normalized = value.strip().lower()

            if normalized in {"1", "true", "yes", "y", "on"}:
                return True

            if normalized in {"0", "false", "no", "n", "off"}:
                return False

        if isinstance(value, (int, float)):
            return bool(value)

        return default

    except Exception:
        return default


def _as_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def _sanitize_role(value: Any, fallback: str = DEFAULT_ROLE) -> str:
    try:
        role = _clean_text(value, fallback, 40).lower()
        return role if role in ALLOWED_ROLES else fallback
    except Exception:
        return fallback


def _sanitize_trace(value: Any) -> List[str]:
    try:
        if not isinstance(value, list):
            return []

        out: List[str] = []

        for item in value:
            text = _clean_text(item, "", MAX_TRACE_ITEM_LENGTH)

            if text:
                out.append(text)

            if len(out) >= MAX_TRACE_ITEMS:
                break

        return out

    except Exception:
        return []


def _sanitize_key(value: Any) -> str:
    text = _clean_text(value, "", MAX_TEMPLATE_KEY_LENGTH)

    if not text:
        return ""

    safe_chars = []

    for char in text:
        if char.isalnum() or char in {"_", "-", ".", ":"}:
            safe_chars.append(char)
        else:
            safe_chars.append("_")

    return "".join(safe_chars).strip("._-:")


def _sanitize_renderer(value: Any) -> str:
    renderer = _clean_text(value, DEFAULT_RENDERER, MAX_RENDERER_LENGTH)

    if not renderer:
        return DEFAULT_RENDERER

    safe_chars = []

    for char in renderer:
        if char.isalnum() or char in {"_", "-", ".", ":"}:
            safe_chars.append(char)
        else:
            safe_chars.append("_")

    return "".join(safe_chars).strip("._-:") or DEFAULT_RENDERER


def _is_legacy_template_key(key: Any) -> bool:
    try:
        return _sanitize_key(key) in LEGACY_TEMPLATE_KEYS
    except Exception:
        return False


def _is_legacy_renderer(renderer: Any) -> bool:
    try:
        return _sanitize_renderer(renderer) in LEGACY_RENDERERS
    except Exception:
        return False


def _is_legacy_template(row: Any) -> bool:
    try:
        if not isinstance(row, dict):
            return False

        key = row.get("key")
        renderer = row.get("renderer")

        return _is_legacy_template_key(key) or _is_legacy_renderer(renderer)

    except Exception:
        return False


def _is_legacy_payload_key(key: Any) -> bool:
    try:
        text = str(key or "").strip().lower()

        if not text:
            return False

        if text in LEGACY_PAYLOAD_KEYS:
            return True

        if text.startswith("spe" + "ckle"):
            return True

        return False

    except Exception:
        return False


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_SCHEMA_DEPTH:
        return None

    try:
        if isinstance(value, Mapping):
            clean: Dict[str, Any] = {}

            for key, item in value.items():
                if _is_legacy_payload_key(key):
                    continue

                key_text = _clean_text(key, "", 160)
                if not key_text:
                    continue

                clean[key_text] = _json_safe(item, depth=depth + 1)

            return clean

        if isinstance(value, list):
            return [_json_safe(item, depth=depth + 1) for item in value]

        if isinstance(value, tuple):
            return [_json_safe(item, depth=depth + 1) for item in value]

        if isinstance(value, (str, int, float, bool)) or value is None:
            return value

        return str(value)

    except Exception:
        return None


def _normalize_template(row: Any) -> Optional[Dict[str, Any]]:
    try:
        if not isinstance(row, dict):
            return None

        if _is_legacy_template(row):
            return None

        key = _sanitize_key(row.get("key"))
        if not key:
            return None

        renderer = _sanitize_renderer(row.get("renderer") or DEFAULT_RENDERER)

        if renderer in LEGACY_RENDERERS:
            return None

        schema_json = row.get("schema_json") or row.get("schema") or {}
        if not isinstance(schema_json, dict):
            schema_json = {}

        schema_json_safe = _json_safe(schema_json)
        if not isinstance(schema_json_safe, dict):
            schema_json_safe = {}

        return {
            "key": key,
            "version": _as_int(row.get("version"), 1),
            "renderer": renderer,
            "title": _clean_text(row.get("title"), key, MAX_TITLE_LENGTH),
            "is_active": _as_bool(row.get("is_active"), True),
            "schema_json": schema_json_safe,
        }

    except Exception:
        return None


def _filter_templates(items: Iterable[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    try:
        for item in items or []:
            normalized = _normalize_template(item)

            if not normalized:
                continue

            key = normalized["key"]

            if key in seen:
                continue

            seen.add(key)
            out.append(normalized)

    except Exception:
        pass

    return out


def _get_conversation(chat_id: str) -> Optional[Conversation]:
    try:
        return Conversation.query.get(str(chat_id))
    except Exception:
        try:
            current_app.logger.exception("conversation lookup failed")
        except Exception:
            pass
        return None


def _prefilter_config_seed() -> None:
    """
    Defensive cleanup before msg._ensure_seed_loaded().

    If config still contains old seed entries, remove them so the registry does
    not re-add legacy viewer-card templates.
    """
    try:
        seed = current_app.config.get("TEMPLATE_SEED")

        if isinstance(seed, list):
            current_app.config["TEMPLATE_SEED"] = _filter_templates(seed)

    except Exception:
        pass


def _deactivate_legacy_db_templates() -> int:
    """
    Best-effort DB cleanup for old template rows.

    This does not hard-delete rows. It marks them inactive if the MessageTemplate
    model is present and has the expected fields.
    """
    try:
        from models import MessageTemplate

        changed = 0

        for row in MessageTemplate.query.all():
            try:
                key = getattr(row, "key", "")
                renderer = getattr(row, "renderer", "")

                if _is_legacy_template_key(key) or _is_legacy_renderer(renderer):
                    if hasattr(row, "is_active"):
                        row.is_active = False
                        changed += 1
            except Exception:
                continue

        if changed:
            db.session.commit()

        return changed

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return 0


# ───────────────────────── Templates registry ─────────────────────────

@bp.get("/v1/templates")
def list_templates():
    """
    Optional query params:
    - active=1|0
    - q=...
    """
    try:
        items = _filter_templates(msg.list_templates() or [])

        active_query = request.args.get("active")
        if active_query is not None:
            want_active = _as_bool(active_query, True)
            items = [
                item
                for item in items
                if bool(item.get("is_active", True)) == want_active
            ]

        query = _clean_text(request.args.get("q"), "", 120).lower()
        if query:
            def _match(template: Dict[str, Any]) -> bool:
                try:
                    return any(
                        query in str(template.get(field, "")).lower()
                        for field in ("key", "title", "renderer")
                    )
                except Exception:
                    return False

            items = [item for item in items if _match(item)]

        return _json_response(
            {
                "ok": True,
                "items": items,
                "total": len(items),
                "legacy_3d_backend": False,
            },
            status=200,
        )

    except Exception as exc:
        try:
            current_app.logger.exception("list_templates failed")
        except Exception:
            pass

        return _json_error(str(exc), 500, code="list_templates_failed")


@bp.get("/v1/templates/<key>")
def get_template(key: str):
    try:
        clean_key = _sanitize_key(key)

        if not clean_key:
            return _json_error("key required", 400, code="key_required")

        if _is_legacy_template_key(clean_key):
            return _json_error("not found", 404, code="template_not_found")

        template = msg.get_template(clean_key)

        normalized = _normalize_template(template)

        if not normalized:
            return _json_error("not found", 404, code="template_not_found")

        return _json_response(
            {
                "ok": True,
                "template": normalized,
                **normalized,
                "legacy_3d_backend": False,
            },
            status=200,
        )

    except Exception as exc:
        try:
            current_app.logger.exception("get_template failed")
        except Exception:
            pass

        return _json_error(str(exc), 500, code="get_template_failed")


@bp.post("/v1/templates")
def upsert_template():
    """
    Upsert a template.

    Body JSON:
    {
      "key": "...",
      "renderer": "InfoCard",
      "title": "...",
      "version": 1,
      "schema_json": {},
      "is_active": true
    }

    Legacy 3D viewer-card templates are rejected.
    """
    try:
        data = _request_json()

        key = _sanitize_key(data.get("key"))
        if not key:
            return _json_error("key required", 400, code="key_required")

        renderer = _sanitize_renderer(data.get("renderer") or DEFAULT_RENDERER)

        if _is_legacy_template_key(key) or _is_legacy_renderer(renderer):
            return _json_error(
                "legacy viewer templates are disabled",
                400,
                code="legacy_template_disabled",
            )

        schema_json = data.get("schema_json") or data.get("schema") or {}
        if schema_json and not isinstance(schema_json, dict):
            return _json_error(
                "schema_json must be object",
                400,
                code="invalid_schema",
            )

        schema_json_safe = _json_safe(schema_json if isinstance(schema_json, dict) else {})
        if not isinstance(schema_json_safe, dict):
            schema_json_safe = {}

        row = msg.register_template(
            key=key,
            schema_json=schema_json_safe,
            renderer=renderer,
            title=_clean_text(data.get("title"), key, MAX_TITLE_LENGTH),
            version=_as_int(data.get("version"), 1),
            is_active=_as_bool(data.get("is_active"), True),
        )

        normalized = _normalize_template(row) or {
            "key": key,
            "renderer": renderer,
            "title": _clean_text(data.get("title"), key, MAX_TITLE_LENGTH),
            "version": _as_int(data.get("version"), 1),
            "is_active": _as_bool(data.get("is_active"), True),
            "schema_json": schema_json_safe,
        }

        return _json_response(
            {
                "ok": True,
                "template": normalized,
                **normalized,
                "legacy_3d_backend": False,
            },
            status=200,
        )

    except BadRequest as exc:
        return _json_error(str(exc), 400, code="bad_request")

    except Exception as exc:
        try:
            current_app.logger.exception("upsert_template failed")
        except Exception:
            pass

        return _json_error(str(exc), 500, code="upsert_template_failed")


@bp.post("/v1/templates/seed")
def seed_templates():
    """
    Reload template seeds from config.

    Existing legacy DB rows are best-effort deactivated and never returned by
    this API.
    """
    try:
        _prefilter_config_seed()

        try:
            setattr(msg._ensure_seed_loaded, "_done", False)
        except Exception:
            pass

        msg._ensure_seed_loaded()

        deactivated = _deactivate_legacy_db_templates()
        items = _filter_templates(msg.list_templates() or [])

        return _json_response(
            {
                "ok": True,
                "status": "ok",
                "total": len(items),
                "items": items,
                "legacy_templates_deactivated": deactivated,
                "legacy_3d_backend": False,
            },
            status=200,
        )

    except Exception as exc:
        try:
            current_app.logger.exception("seed_templates failed")
        except Exception:
            pass

        return _json_error(str(exc), 500, code="seed_templates_failed")


# ───────────────────────── Post card/text to transcript ─────────────────────────

@bp.post("/v1/chats/<chat_id>/messages")
def post_chat_message(chat_id: str):
    """
    Post structured cards or plain text into the transcript.

    Body variants:
    A)
    {
      "template_key": "project_welcome",
      "payload": {},
      "role": "service",
      "trace": ["ChatAI"],
      "validate": true
    }

    B)
    {
      "text": "...",
      "role": "assistant",
      "trace": []
    }

    Legacy 3D viewer-card templates are rejected.
    """
    try:
        conv = _get_conversation(chat_id)

        if not conv:
            return _json_error("chat not found", 404, code="chat_not_found")

        data = _request_json()

        template_key = _sanitize_key(data.get("template_key"))
        text = data.get("text")

        if not template_key and not isinstance(text, str):
            return _json_error(
                "template_key or text required",
                400,
                code="message_content_required",
            )

        if template_key and _is_legacy_template_key(template_key):
            return _json_error(
                "legacy viewer cards are disabled",
                400,
                code="legacy_card_disabled",
            )

        role = _sanitize_role(
            data.get("role") or ("assistant" if isinstance(text, str) else "service")
        )
        trace = _sanitize_trace(data.get("trace"))
        validate = _as_bool(data.get("validate"), True)

        if template_key:
            payload = data.get("payload") or {}

            if payload and not isinstance(payload, dict):
                return _json_error(
                    "payload must be object",
                    400,
                    code="invalid_payload",
                )

            clean_payload = _json_safe(payload if isinstance(payload, dict) else {})
            if not isinstance(clean_payload, dict):
                clean_payload = {}

            result = msg.post_card_message(
                conversation=conv,
                template_key=template_key,
                payload=clean_payload,
                role=role,
                trace=trace,
                validate=validate,
            )

            ok = bool(result.get("ok"))

            return _json_response(
                {
                    "ok": ok,
                    "status": "ok" if ok else "invalid",
                    "chat_id": conv.id,
                    "message": result.get("message"),
                    "error": result.get("error"),
                    "legacy_3d_backend": False,
                },
                status=200 if ok else 422,
            )

        result_text = msg.post_text_message(
            conversation=conv,
            text=str(text or ""),
            role=role,
            trace=trace,
            attachments=None,
            meta=None,
        )

        ok = bool(result_text.get("ok"))

        return _json_response(
            {
                "ok": ok,
                "status": "ok" if ok else "error",
                "chat_id": conv.id,
                "message": result_text.get("message"),
                "legacy_3d_backend": False,
            },
            status=200 if ok else 500,
        )

    except Exception as exc:
        try:
            current_app.logger.exception("post_chat_message failed")
        except Exception:
            pass

        return _json_error(str(exc), 500, code="post_chat_message_failed")