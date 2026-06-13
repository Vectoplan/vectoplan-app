# /services/app/routes/ui/viewer2d/cad_embed.py
from __future__ import annotations

from urllib.parse import quote
from typing import Optional, Dict, Any

from flask import jsonify, current_app, request, url_for

from models import Conversation
from . import bp
from .helpers import (
    _find_latest_dxf_version,
    _cfg_cad_internal_base,
    _cfg_cad_public_base,
    _build_cad_name,
    _load_blob_bytes,
    _is_local_path,
    _fetch_local_path_bytes,
)

def _as_bool(v) -> bool:
    try:
        if isinstance(v, bool):
            return v
        s = str(v or "").strip().lower()
        return s in {"1", "true", "yes", "y", "on"}
    except Exception:
        return False


@bp.get("/ui/chat/<chat_id>/cad-embed.json")
def cad_embed_json(chat_id: str):
    """
    Liefert die Iframe-URL für den CAD-Microservice.

    Ablauf:
      1) jüngste DXF-Version bestimmen (oder 404)
      2) Ziel-Dateiname für den CAD-Service bilden
      3) prüfen, ob Datei im CAD-Service bereits vorhanden (GET /api/v1/files)
      4) falls nötig, Datei hochladen (POST /api/v1/files, Feld 'file')
      5) öffentliche Embed-URL zurückgeben: <PUBLIC_BASE>/embed?file=<NAME>

    Query-Parameter:
      - force=1           → Upload erzwingen, auch wenn Datei bereits vorhanden
      - prefer=blob|url   → Datenquelle bevorzugen (Standard: blob, dann local url)
    """
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404

    info: Optional[Dict[str, Any]] = _find_latest_dxf_version(conv.id)
    if not info:
        # Optionaler Fallback über Template
        fallback = str(current_app.config.get("PLAN2D_FALLBACK_URL_TEMPLATE") or "").strip()
        if not fallback:
            return jsonify({"chat_id": conv.id, "status": "no_plan"}), 404
        dxf_url = fallback.format(chat_id=conv.id)
        label = "plan.dxf"
        rev = 0
        blob_id = None
    else:
        dxf_url = info.get("url") or ""
        label = info.get("label") or "plan.dxf"
        rev = info.get("rev") or 0
        blob_id = info.get("blob_id")

        if not dxf_url and blob_id:
            try:
                dxf_url = url_for("ui_2dviewer.dxf_blob", chat_id=conv.id, blob_id=int(blob_id))
            except Exception:
                dxf_url = ""

    # Zielname und Basis-URLs
    cad_base_internal = _cfg_cad_internal_base().rstrip("/")
    cad_base_public = _cfg_cad_public_base().rstrip("/")
    name = _build_cad_name(conv.id, label, rev)
    embed_url = f"{cad_base_public}/embed?file={quote(name)}"

    # Optionen
    force = _as_bool(request.args.get("force"))
    prefer = (request.args.get("prefer") or "blob").strip().lower()  # 'blob' | 'url'

    # 1) Existenz im CAD-Service prüfen
    present = False
    try:
        from .helpers import _http_get_json  # lazy import
        ok, listing = _http_get_json(f"{cad_base_internal}/api/v1/files")
        if ok:
            # akzeptiere list[str] oder dict{"files":[...]}
            files = listing if isinstance(listing, list) else (listing.get("files") if isinstance(listing, dict) else [])
            present = isinstance(files, list) and name in files
    except Exception:
        present = False

    if present and not force:
        return jsonify({
            "chat_id": conv.id,
            "status": "ok",
            "embed_url": embed_url,
            "dxf_url": dxf_url,
            "file": name,
            "rev": rev,
            "already_present": True,
        }), 200

    # 2) Daten beschaffen: bevorzugt Blob-Bytes, sonst lokale HTTP-Bytes
    data_bytes: Optional[bytes] = None
    try:
        if prefer == "blob" and blob_id is not None:
            data_bytes = _load_blob_bytes(blob_id)
    except Exception:
        data_bytes = None

    if data_bytes is None:
        try:
            if _is_local_path(dxf_url):
                data_bytes = _fetch_local_path_bytes(dxf_url)
        except Exception:
            data_bytes = None

    if data_bytes is None:
        return jsonify({
            "chat_id": conv.id,
            "status": "error",
            "error": "DXF bytes not available for upload",
        }), 500

    # 3) Upload an CAD-Service
    try:
        from .helpers import _http_post_file  # lazy import
        ok, resp = _http_post_file(
            f"{cad_base_internal}/api/v1/files",
            field_name="file",
            filename=name,
            data=data_bytes,
            content_type="application/dxf",
            timeout=60.0,
        )
        if not ok:
            return jsonify({
                "chat_id": conv.id,
                "status": "error",
                "error": "upload to CAD service failed",
            }), 502
    except Exception:
        return jsonify({
            "chat_id": conv.id,
            "status": "error",
            "error": "upload to CAD service failed",
        }), 502

    # 4) Erfolg
    return jsonify({
        "chat_id": conv.id,
        "status": "ok",
        "embed_url": embed_url,
        "dxf_url": dxf_url,
        "file": name,
        "rev": rev,
        "already_present": False,
    }), 200
