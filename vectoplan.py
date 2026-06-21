# services/app/vectoplan.py
from __future__ import annotations

"""
Compatibility facade for the removed legacy 3D backend.

This file intentionally does not import from src.vectoplan anymore.

Purpose:
- keep old imports from older routes temporarily non-breaking
- return the local editor iframe route where older code expects a viewer_url
- prevent accidental uploads/publishes into the removed legacy backend
- provide safe no-op implementations for old ensure/bootstrap functions

Non-responsibilities:
- no database access
- no editor implementation
- no external viewer proxy
- no file upload/publish
- no project/model/version creation
"""

import logging
import os
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

try:
    from flask import current_app, has_app_context
except Exception:  # pragma: no cover - allows importing outside Flask
    current_app = None  # type: ignore
    has_app_context = None  # type: ignore


logger = logging.getLogger(__name__)


# ───────────────────────── Constants ─────────────────────────

DEFAULT_EDITOR_PUBLIC_URL = "http://localhost:5100"
DEFAULT_EDITOR_ROUTE = "/editor"

LEGACY_BACKEND_ENABLED = False


# ───────────────────────── Config helpers ─────────────────────────

def _has_flask_context() -> bool:
    try:
        return bool(has_app_context and has_app_context())
    except Exception:
        return False


def _config_get(keys: Iterable[str], default: str = "") -> str:
    """
    Read config value from Flask config first, then environment.

    Accepts multiple keys to support old/new aliases.
    """
    for key in keys:
        try:
            if _has_flask_context() and current_app is not None:
                value = current_app.config.get(key)
                if value is not None and str(value).strip() != "":
                    return str(value).strip()
        except Exception:
            pass

        try:
            value = os.environ.get(key)
            if value is not None and str(value).strip() != "":
                return str(value).strip()
        except Exception:
            pass

    return default


def _cfg() -> Dict[str, Any]:
    """
    Compatibility config snapshot.

    Older code imported `_cfg` from this module. Keep it available, but expose
    only neutral editor integration config.
    """
    return {
        "legacy_backend_enabled": False,
        "editor_public_url": _config_get(
            ("VECTOPLAN_EDITOR_PUBLIC_URL", "EDITOR_PUBLIC_URL"),
            DEFAULT_EDITOR_PUBLIC_URL,
        ),
        "editor_route": _config_get(
            ("VECTOPLAN_EDITOR_ROUTE", "EDITOR_ROUTE"),
            DEFAULT_EDITOR_ROUTE,
        ),
        "auto_upload_attachments": False,
    }


# ───────────────────────── URL helpers ─────────────────────────

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


def _chat_id_from(conv: Any) -> str:
    try:
        value = getattr(conv, "id", None)

        if value is None and isinstance(conv, dict):
            value = conv.get("id") or conv.get("chat_id")

        return _clean_text(value, "", 160)

    except Exception:
        return ""


def _normalize_route(route: str) -> str:
    try:
        text = str(route or "").strip()

        if not text:
            return DEFAULT_EDITOR_ROUTE

        if not text.startswith("/"):
            text = "/" + text

        while "//" in text:
            text = text.replace("//", "/")

        return text

    except Exception:
        return DEFAULT_EDITOR_ROUTE


def _join_url(base_url: str, route: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    path = _normalize_route(route)

    if not base:
        base = DEFAULT_EDITOR_PUBLIC_URL

    return base + path


def _append_query(url: str, params: Dict[str, Any]) -> str:
    try:
        parts = urlsplit(url)

        existing = dict(parse_qsl(parts.query, keep_blank_values=False))
        merged: Dict[str, str] = {}

        for key, value in existing.items():
            if value is not None and str(value).strip() != "":
                merged[str(key)] = str(value)

        for key, value in params.items():
            if value is None:
                continue

            value_text = str(value).strip()
            if value_text == "":
                continue

            merged[str(key)] = value_text

        query = urlencode(merged, doseq=False)

        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                query,
                parts.fragment,
            )
        )

    except Exception:
        return url


def _local_editor_route(chat_id: str) -> str:
    safe_chat_id = quote(str(chat_id or "").strip(), safe="")

    if safe_chat_id:
        return f"/ui/chat/{safe_chat_id}/editor"

    return "/ui/editor"


def _public_editor_url(chat_id: str = "") -> str:
    public_url = _config_get(
        ("VECTOPLAN_EDITOR_PUBLIC_URL", "EDITOR_PUBLIC_URL"),
        DEFAULT_EDITOR_PUBLIC_URL,
    )
    editor_route = _config_get(
        ("VECTOPLAN_EDITOR_ROUTE", "EDITOR_ROUTE"),
        DEFAULT_EDITOR_ROUTE,
    )

    base = _join_url(public_url, editor_route)

    params: Dict[str, Any] = {
        "embed": "1",
    }

    if chat_id:
        params["chat_id"] = chat_id

    return _append_query(base, params)


def editor_url(conv: Any = None, *, public: bool = False, **_: Any) -> str:
    """
    Return the editor URL.

    Default:
      local app route, because the iframe should go through /ui/chat/<id>/editor.

    public=True:
      direct browser-facing editor URL, useful for diagnostics only.
    """
    try:
        chat_id = _chat_id_from(conv)

        if public:
            return _public_editor_url(chat_id)

        return _local_editor_route(chat_id)

    except Exception:
        return "/ui/editor"


def viewer_url(conv: Any = None, *args: Any, **kwargs: Any) -> str:
    """
    Compatibility name for old code.

    Returns the local editor iframe route. It never returns an old external
    viewer URL.
    """
    try:
        return editor_url(conv, public=False)
    except Exception:
        return "/ui/editor"


# ───────────────────────── Legacy no-op functions ─────────────────────────

def ensure_and_refresh(conv: Any = None, *args: Any, **kwargs: Any) -> Any:
    """
    Compatibility no-op.

    Old behavior used to ensure external 3D backend state. That backend is
    removed. This function now returns the passed conversation unchanged.
    """
    try:
        return conv
    except Exception:
        return conv


def ensure_model(conv: Any = None, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    """
    Compatibility no-op for old model creation.

    No project/model is created here.
    """
    try:
        return {
            "ok": True,
            "created": False,
            "changed": False,
            "legacy_backend_enabled": False,
            "editor_url": editor_url(conv),
        }
    except Exception:
        return {
            "ok": False,
            "created": False,
            "changed": False,
            "legacy_backend_enabled": False,
        }


def ensure_placeholder_if_empty(conv: Any = None, *args: Any, **kwargs: Any) -> bool:
    """
    Compatibility no-op.

    No placeholder geometry/model is created.
    """
    try:
        return False
    except Exception:
        return False


def ensure_bootstrap_viewer(conv: Any = None, *args: Any, **kwargs: Any) -> str:
    """
    Compatibility helper returning the editor iframe route.
    """
    try:
        return editor_url(conv)
    except Exception:
        return "/ui/editor"


def upload_file_to_project(
    conv: Any = None,
    blob: Any = None,
    model_name: Optional[str] = None,
    file_ext: Optional[str] = None,
    *args: Any,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Compatibility no-op for old 3D upload/publish calls.

    This function does not upload, publish or mutate remote state.
    It returns enough metadata for old callers to handle the file as stored-only.
    """
    try:
        file_id = getattr(blob, "id", None)
        filename = getattr(blob, "filename", None)
        mime = getattr(blob, "mime", None)
        size = getattr(blob, "size", None)
        sha = getattr(blob, "sha256", None)
        ext = file_ext or ""

        if not ext and filename:
            try:
                _, dot, suffix = str(filename).rpartition(".")
                if dot and suffix:
                    ext = "." + suffix.lower()
            except Exception:
                ext = ""

        url = editor_url(conv)

        return {
            "ok": False,
            "status": "disabled",
            "reason": "legacy_3d_upload_disabled",
            "stored_only": True,
            "legacy_backend_enabled": False,
            "file_id": file_id,
            "filename": filename,
            "mime": mime,
            "size": size,
            "sha256": sha,
            "ext": ext,
            "model_name": model_name,
            "viewer_url": url,
            "editor_url": url,
        }

    except Exception as exc:
        try:
            logger.warning("upload_file_to_project compatibility no-op failed: %s", exc)
        except Exception:
            pass

        return {
            "ok": False,
            "status": "disabled",
            "reason": "legacy_3d_upload_disabled",
            "stored_only": True,
            "legacy_backend_enabled": False,
        }


# ───────────────────────── Module exports ─────────────────────────

__all__ = [

    "_cfg",
    "editor_url",
    "viewer_url",
    "ensure_and_refresh",
    "ensure_model",
    "ensure_placeholder_if_empty",
    "ensure_bootstrap_viewer",
    "upload_file_to_project",
]