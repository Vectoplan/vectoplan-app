# /services/app/routes/ui/viewer2d/pages.py
from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, Optional

from flask import (
    render_template,
    request,
    current_app,
    make_response,
    url_for,
    jsonify,
)

from models import Conversation, Blob
from . import bp
from .helpers import (
    _ensure_conv,
    _cache_headers,
    _frame_headers,
    _find_latest_dxf_version,
    _is_local_path,
    _conversation_has_blob,
    _is_dxf_name,
)


# ───────────────────────── intern: Fallback-URL aus Config ─────────────────────────

def _fallback_url_for(conv_id: str) -> str:
    """
    Nutzt optional PLAN2D_FALLBACK_URL_TEMPLATE aus config:
      z.B. "https://cdn.example.com/plans/{chat_id}/default.dxf"
    """
    try:
      tmpl = current_app.config.get("PLAN2D_FALLBACK_URL_TEMPLATE", "")
      return (tmpl or "").format(chat_id=conv_id) if tmpl else ""
    except Exception:
      return ""


# ───────────────────────── pages (HTML) ─────────────────────────

@bp.get("/ui/chat/<chat_id>/cad2d")
def cad2d_page(chat_id: str):
    """
    Iframe-Seite für den 2D-Viewer (Legacy-Variante).
    Im neuen Flow nicht zwingend benötigt, bleibt aber kompatibel.
    """
    conv = _ensure_conv(chat_id)

    try:
        plan2d_url = url_for("ui_2dviewer.plan2d_json", chat_id=conv.id)
    except Exception:
        plan2d_url = f"/ui/chat/{conv.id}/plan2d.json"

    resp = make_response(
        render_template("viewer/cad2d.html", chat_id=conv.id, plan2d_url=plan2d_url)
    )
    _cache_headers(resp, strong=False)
    _frame_headers(resp)
    return resp


# ───────────────────────── data (JSON) ─────────────────────────

@bp.get("/ui/chat/<chat_id>/plan2d.json")
def plan2d_json(chat_id: str):
    """
    Liefert Metadaten für den 2D-Viewer (DXF-Quelle).
    Wird weiterhin genutzt, u. a. um Raw-Links im UI zu zeigen.
    Antwort:
      200: { chat_id, dxf_url, title, rev, updated_at, source }
      403: { status:"blocked", message, source } (externe URL nicht erlaubt)
      404: { status:"no_plan", message }
    """
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404

    src = "unknown"
    label = "plan.dxf"
    dxf_url = ""
    rev = 0
    updated = None

    view_only = bool(current_app.config.get("VIEW_ONLY_MODE"))
    allow_cdn = bool(current_app.config.get("ALLOW_CDN"))

    # VIEW_ONLY → bevorzugt Fallback
    if view_only:
        u = _fallback_url_for(conv.id)
        if u:
            dxf_url = u
            src = "config-template"
        else:
            info = _find_latest_dxf_version(conv.id)
            if info:
                label = info.get("label") or label
                rev = int(info.get("rev") or 0)
                updated = info.get("created_at")
                if info.get("url"):
                    dxf_url = info["url"]; src = "version-meta-url"
                elif info.get("blob_id"):
                    try:
                        dxf_url = url_for("ui_2dviewer.dxf_blob", chat_id=conv.id, blob_id=int(info["blob_id"]))
                    except Exception:
                        dxf_url = f"/ui/chat/{conv.id}/file/{int(info['blob_id'])}.dxf"
                    src = "version-blob"

    # Normalbetrieb
    if not view_only and not dxf_url:
        info = _find_latest_dxf_version(conv.id)
        if info:
            label = info.get("label") or label
            rev = int(info.get("rev") or 0)
            updated = info.get("created_at")
            if info.get("url"):
                dxf_url = info["url"]; src = "version-meta-url"
            elif info.get("blob_id"):
                try:
                    dxf_url = url_for("ui_2dviewer.dxf_blob", chat_id=conv.id, blob_id=int(info["blob_id"]))
                except Exception:
                    dxf_url = f"/ui/chat/{conv.id}/file/{int(info['blob_id'])}.dxf"
                src = "version-blob"

    # Fallback (z. B. statische Testdatei)
    if not dxf_url:
        u = _fallback_url_for(conv.id)
        if u:
            dxf_url = u; src = "config-template"

    # externe URLs blocken (falls nicht erlaubt)
    if dxf_url and not _is_local_path(dxf_url) and not allow_cdn:
        return jsonify({
            "chat_id": conv.id,
            "status": "blocked",
            "message": "external DXF URLs are disabled",
            "source": src,
        }), 403

    if not dxf_url:
        return jsonify({
            "chat_id": conv.id,
            "status": "no_plan",
            "message": "no DXF found for conversation",
        }), 404

    payload = {
        "chat_id": conv.id,
        "dxf_url": dxf_url,
        "title": label,
        "rev": rev,
        "updated_at": updated,
        "source": src,
    }
    resp = make_response(jsonify(payload), 200)
    _cache_headers(resp, strong=False)
    return resp


@bp.get("/ui/chat/<chat_id>/file/<int:blob_id>.dxf")
def dxf_blob(chat_id: str, blob_id: int):
    """
    Liefert eine im Blob gespeicherte DXF-Datei aus,
    falls sie in einer Version der Conversation referenziert ist.
    """
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404

    if not _conversation_has_blob(conv.id, blob_id):
        return jsonify({"error": "forbidden or blob not linked to conversation"}), 404

    blob = Blob.query.get(blob_id)
    if not blob or not blob.data:
        return jsonify({"error": "blob not found"}), 404

    if not _is_dxf_name(blob.filename):
        return jsonify({"error": "blob is not a DXF"}), 400

    raw = blob.data if isinstance(blob.data, (bytes, bytearray)) else bytes(blob.data)
    data = BytesIO(raw)
    resp = make_response(data.getvalue())
    resp.headers["Content-Type"] = blob.mime or "application/dxf"
    resp.headers["Content-Length"] = str(len(raw))
    disp = f'inline; filename="{(blob.filename or "plan.dxf").split("/")[-1]}"'
    resp.headers["Content-Disposition"] = disp
    if getattr(blob, "sha256", None):
        # Starkes ETag + starker Cache
        resp.headers["ETag"] = blob.sha256
        _cache_headers(resp, strong=True)
    else:
        _cache_headers(resp, strong=False)
    _frame_headers(resp)
    return resp
