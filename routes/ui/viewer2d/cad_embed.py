# services/app/routes/ui/viewer2d/cad_embed.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

from flask import current_app, jsonify, request, url_for

from models import Conversation

from . import bp
from .helpers import (
    _build_cad_name,
    _cfg_cad_internal_base,
    _cfg_cad_public_base,
    _fetch_local_path_bytes,
    _find_latest_dxf_version,
    _is_local_path,
    _load_blob_bytes,
)


# ───────────────────────── Constants ─────────────────────────

DEFAULT_DXF_LABEL = "plan.dxf"
DEFAULT_REVISION = 0
DEFAULT_CAD_UPLOAD_TIMEOUT = 60.0

ALLOWED_PREFER_VALUES = {
    "blob",
    "url",
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


def _get_conversation(chat_id: str) -> Optional[Conversation]:
    try:
        return Conversation.query.get(str(chat_id))
    except Exception:
        try:
            current_app.logger.exception("conversation lookup failed")
        except Exception:
            pass
        return None


def _normalize_prefer(value: Any) -> str:
    try:
        prefer = _clean_text(value, "blob", 20).lower()

        if prefer in ALLOWED_PREFER_VALUES:
            return prefer

        return "blob"

    except Exception:
        return "blob"


def _safe_cad_base_urls() -> Tuple[str, str]:
    """
    Return (internal_base, public_base).

    Raises ValueError if either URL is missing.
    """
    internal_base = ""
    public_base = ""

    try:
        internal_base = _cfg_cad_internal_base().rstrip("/")
    except Exception:
        internal_base = ""

    try:
        public_base = _cfg_cad_public_base().rstrip("/")
    except Exception:
        public_base = ""

    if not internal_base:
        raise ValueError("CAD internal base URL missing")

    if not public_base:
        raise ValueError("CAD public base URL missing")

    return internal_base, public_base


def _latest_dxf_or_fallback(conv: Conversation) -> Tuple[str, str, int, Optional[Any], str]:
    """
    Return:
      (dxf_url, label, revision, blob_id, source)

    source:
      "version" | "fallback"
    """
    info: Optional[Dict[str, Any]] = None

    try:
        info = _find_latest_dxf_version(conv.id)
    except Exception:
        try:
            current_app.logger.warning("find latest DXF version failed", exc_info=True)
        except Exception:
            pass
        info = None

    if info:
        label = _clean_text(info.get("label"), DEFAULT_DXF_LABEL, 240)
        revision = _safe_int(info.get("rev"), DEFAULT_REVISION)
        blob_id = info.get("blob_id") or info.get("input_blob_id")
        dxf_url = _clean_text(info.get("url"), "", 1000)

        if not dxf_url and blob_id is not None:
            try:
                dxf_url = url_for(
                    "ui_2dviewer.dxf_blob",
                    chat_id=conv.id,
                    blob_id=blob_id,
                )
            except Exception:
                dxf_url = ""

        return dxf_url, label, revision, blob_id, "version"

    fallback = _clean_text(
        current_app.config.get("PLAN2D_FALLBACK_URL_TEMPLATE"),
        "",
        1000,
    )

    if not fallback:
        raise LookupError("no DXF plan available")

    try:
        dxf_url = fallback.format(chat_id=conv.id)
    except Exception:
        dxf_url = fallback

    return dxf_url, DEFAULT_DXF_LABEL, DEFAULT_REVISION, None, "fallback"


def _cad_file_present(cad_base_internal: str, name: str) -> bool:
    try:
        from .helpers import _http_get_json

        ok, listing = _http_get_json(f"{cad_base_internal}/api/v1/files")

        if not ok:
            return False

        files = []

        if isinstance(listing, list):
            files = listing

        elif isinstance(listing, dict):
            raw_files = listing.get("files") or listing.get("items") or []
            files = raw_files if isinstance(raw_files, list) else []

        normalized_name = str(name or "")

        return any(str(item) == normalized_name for item in files)

    except Exception:
        return False


def _load_dxf_bytes(
    *,
    blob_id: Optional[Any],
    dxf_url: str,
    prefer: str,
) -> Optional[bytes]:
    """
    Load DXF bytes from blob or local URL.

    No external URL fetching is allowed here.
    """
    sources = ["blob", "url"] if prefer == "blob" else ["url", "blob"]

    for source in sources:
        if source == "blob":
            if blob_id is None:
                continue

            try:
                data = _load_blob_bytes(blob_id)
                if data:
                    return data
            except Exception:
                try:
                    current_app.logger.warning("load DXF blob bytes failed", exc_info=True)
                except Exception:
                    pass

        elif source == "url":
            try:
                if dxf_url and _is_local_path(dxf_url):
                    data = _fetch_local_path_bytes(dxf_url)
                    if data:
                        return data
            except Exception:
                try:
                    current_app.logger.warning("fetch local DXF path bytes failed", exc_info=True)
                except Exception:
                    pass

    return None


def _upload_to_cad_service(
    *,
    cad_base_internal: str,
    name: str,
    data_bytes: bytes,
) -> bool:
    try:
        from .helpers import _http_post_file

        timeout = DEFAULT_CAD_UPLOAD_TIMEOUT

        try:
            timeout = float(
                current_app.config.get("CADVIEWER_UPLOAD_TIMEOUT", DEFAULT_CAD_UPLOAD_TIMEOUT)
            )
        except Exception:
            timeout = DEFAULT_CAD_UPLOAD_TIMEOUT

        ok, _response = _http_post_file(
            f"{cad_base_internal}/api/v1/files",
            field_name=str(current_app.config.get("CADVIEWER_UPLOAD_FIELD", "file") or "file"),
            filename=name,
            data=data_bytes,
            content_type="application/dxf",
            timeout=timeout,
        )

        return bool(ok)

    except Exception:
        try:
            current_app.logger.warning("upload to CAD service failed", exc_info=True)
        except Exception:
            pass
        return False


# ───────────────────────── Route ─────────────────────────

@bp.get("/ui/chat/<chat_id>/cad-embed.json")
def cad_embed_json(chat_id: str):
    """
    Return the iframe URL for the CAD microservice.

    Flow:
    1. resolve the latest local DXF version or configured fallback
    2. build the deterministic CAD service filename
    3. check if the CAD service already has the file
    4. upload DXF bytes to CAD service if necessary
    5. return browser-facing embed URL

    Query parameters:
    - force=1
      Upload even when the CAD service already lists the file.

    - prefer=blob|url
      Prefer blob bytes or local URL bytes.
      Default: blob.
    """
    conv = _get_conversation(chat_id)

    if not conv:
        return _json_error(
            chat_id=chat_id,
            message="not found",
            status=404,
            code="chat_not_found",
        )

    try:
        dxf_url, label, revision, blob_id, source = _latest_dxf_or_fallback(conv)

    except LookupError:
        return _json_response(
            {
                "ok": False,
                "status": "no_plan",
                "chat_id": conv.id,
                "embed_url": None,
                "dxf_url": None,
                "file": None,
                "rev": None,
                "source": None,
                "legacy_3d_backend": False,
            },
            status=404,
        )

    except Exception as exc:
        current_app.logger.exception("cad_embed: resolve DXF failed")
        return _json_error(
            chat_id=conv.id,
            message=str(exc),
            status=500,
            code="resolve_dxf_failed",
        )

    try:
        cad_base_internal, cad_base_public = _safe_cad_base_urls()
    except Exception as exc:
        return _json_error(
            chat_id=conv.id,
            message=str(exc),
            status=500,
            code="cad_config_missing",
        )

    try:
        name = _build_cad_name(conv.id, label, revision)
    except Exception:
        name = f"{conv.id}__{revision}__plan.dxf"

    embed_url = f"{cad_base_public}/embed?file={quote(str(name or 'plan.dxf'))}"

    force = _as_bool(request.args.get("force"), default=False)
    prefer = _normalize_prefer(request.args.get("prefer"))

    present = _cad_file_present(cad_base_internal, name)

    if present and not force:
        return _json_response(
            {
                "ok": True,
                "status": "ok",
                "chat_id": conv.id,
                "embed_url": embed_url,
                "iframe_url": embed_url,
                "dxf_url": dxf_url,
                "file": name,
                "rev": revision,
                "source": source,
                "blob_id": blob_id,
                "already_present": True,
                "uploaded": False,
                "legacy_3d_backend": False,
            },
            status=200,
        )

    data_bytes = _load_dxf_bytes(
        blob_id=blob_id,
        dxf_url=dxf_url,
        prefer=prefer,
    )

    if data_bytes is None:
        return _json_error(
            chat_id=conv.id,
            message="DXF bytes not available for upload",
            status=500,
            code="dxf_bytes_unavailable",
            extra={
                "dxf_url": dxf_url,
                "blob_id": blob_id,
                "source": source,
            },
        )

    uploaded = _upload_to_cad_service(
        cad_base_internal=cad_base_internal,
        name=name,
        data_bytes=data_bytes,
    )

    if not uploaded:
        return _json_error(
            chat_id=conv.id,
            message="upload to CAD service failed",
            status=502,
            code="cad_upload_failed",
            extra={
                "dxf_url": dxf_url,
                "file": name,
                "rev": revision,
                "source": source,
                "blob_id": blob_id,
            },
        )

    return _json_response(
        {
            "ok": True,
            "status": "ok",
            "chat_id": conv.id,
            "embed_url": embed_url,
            "iframe_url": embed_url,
            "dxf_url": dxf_url,
            "file": name,
            "rev": revision,
            "source": source,
            "blob_id": blob_id,
            "already_present": False,
            "uploaded": True,
            "legacy_3d_backend": False,
        },
        status=200,
    )