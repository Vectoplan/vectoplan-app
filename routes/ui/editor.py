# services/app/routes/ui/editor.py
"""
UI integration route for embedding the local VECTOPLAN editor.

This module intentionally does only one thing:
it builds a browser-facing URL to the local `vectoplan-editor` service and
redirects the current iframe request to that editor.

No database access.
No project model logic.
No Conversation lookup.
No Speckle compatibility.
No HTML fallback rendering.
No import/upload/versioning logic.

The central app shell can use this route as iframe src:

    /ui/chat/<chat_id>/editor

The route then redirects to something like:

    http://localhost:5100/editor?chat_id=<chat_id>&embed=1

Configuration keys supported:

    VECTOPLAN_EDITOR_PUBLIC_URL
    VECTOPLAN_EDITOR_ROUTE

Optional fallbacks:

    EDITOR_PUBLIC_URL
    EDITOR_ROUTE

Development defaults:

    VECTOPLAN_EDITOR_PUBLIC_URL=http://localhost:5100
    VECTOPLAN_EDITOR_ROUTE=/editor
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, current_app, jsonify, redirect, request


ui_editor_bp = Blueprint("ui_editor", __name__)

logger = logging.getLogger(__name__)


DEFAULT_EDITOR_PUBLIC_URL = "http://localhost:5100"
DEFAULT_EDITOR_ROUTE = "/editor"

MAX_CHAT_ID_LENGTH = 160
SAFE_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")

ALLOWED_QUERY_KEYS = {
    "chat_id",
    "embed",
    "mode",
    "tool",
    "view",
    "debug",
    "readonly",
    "project_id",
    "world_id",
    "snapshot_id",
    "version_id",
    "artifact_id",
}


@ui_editor_bp.get("/ui/chat/<chat_id>/editor")
def editor_iframe(chat_id: str):
    """
    Redirect the app iframe to the configured vectoplan-editor URL.

    This route deliberately does not validate the chat against the database.
    At this stage the only goal is iframe integration of the editor and removal
    of the old Speckle/viewer path elsewhere.

    Returns:
        302 redirect to the editor on success.
        JSON error response on failure.
    """
    try:
        safe_chat_id = _sanitize_chat_id(chat_id)
        editor_url = _build_editor_url(safe_chat_id)

        response = redirect(editor_url, code=302)
        _apply_no_cache_headers(response)
        return response

    except ValueError as exc:
        logger.warning("Invalid editor iframe request: %s", exc)
        return _json_error(
            status=400,
            code="invalid_editor_request",
            message=str(exc),
        )

    except Exception as exc:  # noqa: BLE001 - route boundary must be defensive
        logger.exception("Failed to build editor iframe URL for chat_id=%r", chat_id)
        return _json_error(
            status=500,
            code="editor_url_failed",
            message="Editor-URL konnte nicht erzeugt werden.",
        )


@ui_editor_bp.get("/ui/editor")
def editor_iframe_without_chat():
    """
    Optional generic editor iframe route.

    Useful for testing the iframe integration before a chat_id exists:

        /ui/editor

    Redirects to:

        http://localhost:5100/editor?embed=1
    """
    try:
        editor_url = _build_editor_url(chat_id=None)

        response = redirect(editor_url, code=302)
        _apply_no_cache_headers(response)
        return response

    except Exception:  # noqa: BLE001 - route boundary must be defensive
        logger.exception("Failed to build generic editor iframe URL")
        return _json_error(
            status=500,
            code="editor_url_failed",
            message="Editor-URL konnte nicht erzeugt werden.",
        )


def _build_editor_url(chat_id: str | None) -> str:
    public_url = _config_value(
        primary="VECTOPLAN_EDITOR_PUBLIC_URL",
        fallback="EDITOR_PUBLIC_URL",
        default=DEFAULT_EDITOR_PUBLIC_URL,
    )
    editor_route = _config_value(
        primary="VECTOPLAN_EDITOR_ROUTE",
        fallback="EDITOR_ROUTE",
        default=DEFAULT_EDITOR_ROUTE,
    )

    base_url = _join_url(public_url, editor_route)
    query = _build_query(chat_id)

    return _append_query(base_url, query)


def _build_query(chat_id: str | None) -> dict[str, str]:
    query: dict[str, str] = {
        "embed": "1",
    }

    if chat_id:
        query["chat_id"] = chat_id

    for key in ALLOWED_QUERY_KEYS:
        if key in {"chat_id", "embed"}:
            continue

        value = request.args.get(key)
        if value is None or value == "":
            continue

        cleaned = _sanitize_query_value(key, value)
        if cleaned:
            query[key] = cleaned

    return query


def _sanitize_chat_id(chat_id: str) -> str:
    value = str(chat_id or "").strip()

    if not value:
        raise ValueError("chat_id fehlt.")

    if len(value) > MAX_CHAT_ID_LENGTH:
        raise ValueError("chat_id ist zu lang.")

    if not SAFE_CHAT_ID_RE.match(value):
        raise ValueError("chat_id enthält ungültige Zeichen.")

    return value


def _sanitize_query_value(key: str, value: Any) -> str:
    text = str(value or "").strip()

    if not text:
        return ""

    if len(text) > 256:
        raise ValueError(f"Query-Parameter '{key}' ist zu lang.")

    if key in {"debug", "readonly"}:
        return "1" if text.lower() in {"1", "true", "yes", "on", "y"} else "0"

    if key in {
        "mode",
        "tool",
        "view",
        "project_id",
        "world_id",
        "snapshot_id",
        "version_id",
        "artifact_id",
    }:
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$", text):
            raise ValueError(f"Query-Parameter '{key}' enthält ungültige Zeichen.")

    return text


def _config_value(*, primary: str, fallback: str, default: str) -> str:
    value = None

    try:
        value = current_app.config.get(primary)
    except RuntimeError:
        value = None

    if value is None or str(value).strip() == "":
        try:
            value = current_app.config.get(fallback)
        except RuntimeError:
            value = None

    if value is None or str(value).strip() == "":
        value = os.environ.get(primary)

    if value is None or str(value).strip() == "":
        value = os.environ.get(fallback)

    if value is None or str(value).strip() == "":
        value = default

    return str(value).strip()


def _join_url(public_url: str, route: str) -> str:
    clean_public_url = str(public_url or "").strip()
    clean_route = str(route or "").strip()

    if not clean_public_url:
        raise ValueError("VECTOPLAN_EDITOR_PUBLIC_URL fehlt.")

    if not clean_route:
        clean_route = DEFAULT_EDITOR_ROUTE

    if not clean_route.startswith("/"):
        clean_route = "/" + clean_route

    if clean_public_url.endswith("/"):
        clean_public_url = clean_public_url[:-1]

    return clean_public_url + clean_route


def _append_query(url: str, query: dict[str, str]) -> str:
    parts = urlsplit(url)

    existing_query = dict(parse_qsl(parts.query, keep_blank_values=False))
    merged_query = {
        **existing_query,
        **query,
    }

    encoded_query = urlencode(merged_query, doseq=False)

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            encoded_query,
            parts.fragment,
        )
    )


def _json_error(*, status: int, code: str, message: str):
    response = jsonify(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
            },
            "status": status,
        }
    )
    response.status_code = status
    _apply_no_cache_headers(response)
    return response


def _apply_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response