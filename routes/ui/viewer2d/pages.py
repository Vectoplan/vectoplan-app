# services/app/routes/ui/viewer2d/pages.py
from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, Optional
from urllib.parse import quote

from flask import (
    current_app,
    jsonify,
    make_response,
    render_template,
    request,
    url_for,
)

from models import Blob, Conversation

from . import bp
from .helpers import (
    _cache_headers,
    _conversation_has_blob,
    _ensure_conv,
    _find_latest_dxf_version,
    _frame_headers,
    _is_dxf_name,
    _is_local_path,
)


# ───────────────────────── Constants ─────────────────────────

DEFAULT_DXF_LABEL = "plan.dxf"
DEFAULT_DXF_MIME = "application/dxf"


# ───────────────────────── Response helpers ─────────────────────────

def _apply_json_headers(resp):
    try:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
    except Exception:
        pass

    return resp


def _json_response(payload: Dict[str, Any], status: int = 200):
    resp = jsonify(payload)
    resp.status_code = status
    return _apply_json_headers(resp)


def _json_error(
    *,
    chat_id: Optional[str],
    message: str,
    status: int,
    code: str,
    extra: Optional[Dict[str, Any]] = None,
):
    payload: Dict[str, Any] = {
        "ok": False,
        "status": "error",
        "chat_id": chat_id,
        "error": {
            "code": code,
            "message": message,
        },
        "legacy_3d_backend": False,
    }

    if extra:
        payload.update(extra)

    return _json_response(payload, status=status)


# ───────────────────────── Generic helpers ─────────────────────────

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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else default
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        text = str(value if value is not None else "").strip().lower()

        if text in {"1", "true", "yes", "y", "on"}:
            return True

        if text in {"0", "false", "no", "n", "off"}:
            return False

        return default

    except Exception:
        return default


def _get_conversation(chat_id: str) -> Optional[Conversation]:
    try:
        return Conversation.query.get(str(chat_id))
    except Exception:
        try:
            current_app.logger.exception("conversation lookup failed")
        except Exception:
            pass
        return None


def _get_blob(blob_id: Any) -> Optional[Blob]:
    if blob_id is None:
        return None

    raw = str(blob_id).strip()

    if not raw:
        return None

    candidates = [raw]

    try:
        candidates.append(int(raw))
    except Exception:
        pass

    for candidate in candidates:
        try:
            blob = Blob.query.get(candidate)
            if blob:
                return blob
        except Exception:
            continue

    return None


def _fallback_url_for(conv_id: str) -> str:
    """
    Resolve optional PLAN2D_FALLBACK_URL_TEMPLATE.

    Example:
      /static/test/plan.dxf
      /static/test/{chat_id}.dxf

    External fallback URLs are handled later by allow_cdn checks.
    """
    try:
        template = _clean_text(
            current_app.config.get("PLAN2D_FALLBACK_URL_TEMPLATE"),
            "",
            1000,
        )

        if not template:
            return ""

        try:
            return template.format(chat_id=conv_id)
        except Exception:
            return template

    except Exception:
        return ""


def _allow_external_dxf_urls() -> bool:
    try:
        return _as_bool(current_app.config.get("ALLOW_CDN"), False)
    except Exception:
        return False


def _view_only_mode() -> bool:
    try:
        return _as_bool(current_app.config.get("VIEW_ONLY_MODE"), False)
    except Exception:
        return False


def _looks_like_dxf_mime(mime: Any) -> bool:
    try:
        text = str(mime or "").strip().lower()
        return "dxf" in text or text in {DEFAULT_DXF_MIME, "application/x-dxf"}
    except Exception:
        return False


def _blob_is_dxf(blob: Blob) -> bool:
    try:
        if _is_dxf_name(getattr(blob, "filename", "") or ""):
            return True

        if _looks_like_dxf_mime(getattr(blob, "mime", "") or ""):
            return True

        return False

    except Exception:
        return False


def _safe_disposition_filename(filename: Any) -> str:
    try:
        name = _clean_text(filename, DEFAULT_DXF_LABEL, 240)
        name = name.replace("\\", "/").split("/")[-1].strip()

        if not name:
            name = DEFAULT_DXF_LABEL

        if not name.lower().endswith(".dxf"):
            name += ".dxf"

        # RFC 5987 compatible fallback below uses quote().
        ascii_name = "".join(
            char if char.isalnum() or char in {"_", "-", "."} else "_"
            for char in name
        ).strip("._-")

        if not ascii_name:
            ascii_name = DEFAULT_DXF_LABEL

        if not ascii_name.lower().endswith(".dxf"):
            ascii_name += ".dxf"

        return ascii_name

    except Exception:
        return DEFAULT_DXF_LABEL


def _dxf_blob_url(chat_id: str, blob_id: Any) -> str:
    try:
        return url_for(
            "ui_2dviewer.dxf_blob",
            chat_id=chat_id,
            blob_id=blob_id,
        )
    except Exception:
        return f"/ui/chat/{quote(str(chat_id), safe='')}/file/{quote(str(blob_id), safe='')}.dxf"


def _version_info_to_plan(conv: Conversation, info: Dict[str, Any]) -> Dict[str, Any]:
    label = _clean_text(info.get("label"), DEFAULT_DXF_LABEL, 240)
    rev = info.get("rev") if info.get("rev") is not None else 0
    updated = info.get("created_at")
    blob_id = info.get("blob_id") or info.get("input_blob_id")
    dxf_url = _clean_text(info.get("url"), "", 1000)
    source = "version-meta-url" if dxf_url else "version-blob"

    if not dxf_url and blob_id is not None:
        dxf_url = _dxf_blob_url(conv.id, blob_id)

    return {
        "chat_id": conv.id,
        "dxf_url": dxf_url,
        "title": label,
        "rev": rev,
        "updated_at": updated,
        "source": source,
        "blob_id": blob_id,
    }


def _fallback_plan(conv: Conversation) -> Optional[Dict[str, Any]]:
    fallback_url = _fallback_url_for(conv.id)

    if not fallback_url:
        return None

    return {
        "chat_id": conv.id,
        "dxf_url": fallback_url,
        "title": DEFAULT_DXF_LABEL,
        "rev": 0,
        "updated_at": None,
        "source": "config-template",
        "blob_id": None,
    }


def _resolve_plan(conv: Conversation) -> Optional[Dict[str, Any]]:
    """
    Resolve 2D plan source.

    VIEW_ONLY_MODE:
      prefer configured fallback, then latest DXF version.

    Normal mode:
      prefer latest DXF version, then configured fallback.
    """
    try:
        if _view_only_mode():
            fallback = _fallback_plan(conv)
            if fallback:
                return fallback

            info = _find_latest_dxf_version(conv.id)
            if info:
                return _version_info_to_plan(conv, info)

            return None

        info = _find_latest_dxf_version(conv.id)
        if info:
            return _version_info_to_plan(conv, info)

        return _fallback_plan(conv)

    except Exception:
        try:
            current_app.logger.warning("resolve 2D plan failed", exc_info=True)
        except Exception:
            pass
        return None


# ───────────────────────── Pages ─────────────────────────

@bp.get("/ui/chat/<chat_id>/cad2d")
def cad2d_page(chat_id: str):
    """
    Iframe page for the local 2D/CAD viewer.
    """
    conv = _ensure_conv(chat_id)

    try:
        plan2d_url = url_for("ui_2dviewer.plan2d_json", chat_id=conv.id)
    except Exception:
        plan2d_url = f"/ui/chat/{quote(str(conv.id), safe='')}/plan2d.json"

    resp = make_response(
        render_template(
            "viewer/cad2d.html",
            chat_id=conv.id,
            plan2d_url=plan2d_url,
        )
    )

    _cache_headers(resp, strong=False)
    _frame_headers(resp)

    return resp


# ───────────────────────── Data JSON ─────────────────────────

@bp.get("/ui/chat/<chat_id>/plan2d.json")
def plan2d_json(chat_id: str):
    """
    Return metadata for the 2D viewer.

    200:
    {
      ok,
      chat_id,
      dxf_url,
      title,
      rev,
      updated_at,
      source,
      blob_id
    }

    403:
    external DXF URL blocked

    404:
    no DXF plan available
    """
    conv = _get_conversation(chat_id)

    if not conv:
        return _json_error(
            chat_id=chat_id,
            message="not found",
            status=404,
            code="chat_not_found",
        )

    plan = _resolve_plan(conv)

    if not plan or not plan.get("dxf_url"):
        return _json_response(
            {
                "ok": False,
                "status": "no_plan",
                "chat_id": conv.id,
                "message": "no DXF found for conversation",
                "legacy_3d_backend": False,
            },
            status=404,
        )

    dxf_url = _clean_text(plan.get("dxf_url"), "", 1000)

    if dxf_url and not _is_local_path(dxf_url) and not _allow_external_dxf_urls():
        return _json_response(
            {
                "ok": False,
                "status": "blocked",
                "chat_id": conv.id,
                "message": "external DXF URLs are disabled",
                "source": plan.get("source"),
                "legacy_3d_backend": False,
            },
            status=403,
        )

    payload = {
        "ok": True,
        "status": "ok",
        "chat_id": conv.id,
        "dxf_url": dxf_url,
        "title": plan.get("title") or DEFAULT_DXF_LABEL,
        "rev": plan.get("rev") if plan.get("rev") is not None else 0,
        "updated_at": plan.get("updated_at"),
        "source": plan.get("source") or "unknown",
        "blob_id": plan.get("blob_id"),
        "legacy_3d_backend": False,
    }

    return _json_response(payload, status=200)


# ───────────────────────── DXF Blob delivery ─────────────────────────

@bp.get("/ui/chat/<chat_id>/file/<blob_id>.dxf")
def dxf_blob(chat_id: str, blob_id: str):
    """
    Serve a Blob-backed DXF file if it is linked to the conversation through a
    local neutral version entry.
    """
    conv = _get_conversation(chat_id)

    if not conv:
        return _json_error(
            chat_id=chat_id,
            message="not found",
            status=404,
            code="chat_not_found",
        )

    if not _conversation_has_blob(conv.id, blob_id):
        return _json_error(
            chat_id=conv.id,
            message="forbidden or blob not linked to conversation",
            status=404,
            code="blob_not_linked",
        )

    blob = _get_blob(blob_id)

    if not blob or not getattr(blob, "data", None):
        return _json_error(
            chat_id=conv.id,
            message="blob not found",
            status=404,
            code="blob_not_found",
        )

    if not _blob_is_dxf(blob):
        return _json_error(
            chat_id=conv.id,
            message="blob is not a DXF",
            status=400,
            code="not_dxf",
        )

    try:
        raw = blob.data if isinstance(blob.data, (bytes, bytearray)) else bytes(blob.data)
    except Exception:
        return _json_error(
            chat_id=conv.id,
            message="blob bytes unavailable",
            status=500,
            code="blob_bytes_unavailable",
        )

    data = BytesIO(raw)
    resp = make_response(data.getvalue())

    mime = _clean_text(getattr(blob, "mime", None), DEFAULT_DXF_MIME, 160)
    if not _looks_like_dxf_mime(mime):
        mime = DEFAULT_DXF_MIME

    filename = _safe_disposition_filename(getattr(blob, "filename", None))
    encoded_filename = quote(filename)

    resp.headers["Content-Type"] = mime
    resp.headers["Content-Length"] = str(len(raw))
    resp.headers["Content-Disposition"] = (
        f'inline; filename="{filename}"; filename*=UTF-8\'\'{encoded_filename}'
    )
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")

    sha = getattr(blob, "sha256", None)

    if sha:
        resp.headers["ETag"] = str(sha)
        _cache_headers(resp, strong=True)
    else:
        _cache_headers(resp, strong=False)

    _frame_headers(resp)

    return resp