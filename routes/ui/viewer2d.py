# /services/app/routes/ui/viewer2d.py
from __future__ import annotations

import os
import re
import json
from io import BytesIO
from typing import Any, Dict, Optional, List, Tuple
from urllib.parse import urlencode, quote

from flask import (
    Blueprint,
    render_template,
    request,
    current_app,
    make_response,
    url_for,
    jsonify,
    abort,
)

from models import Conversation, Blob

bp = Blueprint("ui_2dviewer", __name__)

# ───────────────────────── helpers ─────────────────────────

_DXF_KINDS = {"BPA_DXF", "BPA_PLAN_DXF", "BPA_PLAN", "DXF_UPLOAD"}


def _ext_of(filename: str) -> str:
    try:
        return os.path.splitext(filename or "")[1].lower()
    except Exception:
        return ""


def _is_dxf_name(name: str | None) -> bool:
    try:
        return _ext_of(name or "") == ".dxf"
    except Exception:
        return False


def _is_local_path(u: str | None) -> bool:
    """
    Nur gleiche-Origin-Pfade (beginnt mit "/" und NICHT mit "//").
    Keine http/https Schemata.
    """
    try:
        if not u:
            return False
        if u.startswith("//"):
            return False
        if u.startswith("http://") or u.startswith("https://"):
            return False
        return u.startswith("/")
    except Exception:
        return False


def _find_latest_dxf_version(conv_id: str) -> Optional[Dict[str, Any]]:
    """
    Sucht die jüngste Version mit DXF-Bezug.
    Erwartete Struktur kompatibel zu versioning.list_versions_by_conversation().
    Rückgabe: Dict mit keys: version, blob_id, url, meta, label, rev, created_at
    """
    try:
        from versioning import list_versions_by_conversation  # lazy import
    except Exception:
        return None

    try:
        items: List[Dict[str, Any]] = list_versions_by_conversation(conversation_id=conv_id, kind=None) or []
    except Exception:
        return None

    def is_candidate(v: Dict[str, Any]) -> bool:
        if str(v.get("kind") or "") in _DXF_KINDS:
            return True
        meta = v.get("meta") or {}
        if _is_dxf_name(meta.get("filename")):
            return True
        if _is_dxf_name(v.get("label")):
            return True
        return False

    cands = [v for v in items if is_candidate(v)]
    if not cands:
        return None

    def ts(v: Dict[str, Any]) -> Any:
        return v.get("created_at") or v.get("created") or v.get("ts") or 0

    cands.sort(key=ts, reverse=True)
    v = cands[0]
    meta = v.get("meta") or {}
    info = {
        "version": v,
        "blob_id": v.get("input_blob_id") or v.get("blob_id"),
        "url": meta.get("dxf_url") or meta.get("url") or "",
        "meta": meta,
        "label": v.get("label") or meta.get("filename") or "plan.dxf",
        "rev": v.get("id") or v.get("version_id") or meta.get("rev") or 0,
        "created_at": ts(v),
    }
    return info


def _ensure_conv(chat_id: Optional[str]) -> Conversation:
    """
    Holt oder erzeugt eine Conversation, analog zu ui_chat.chat_viewer_page.
    """
    conv = Conversation.query.get(chat_id) if chat_id else None
    if conv is not None:
        return conv
    # Erzeugen und redirectfähig zurückgeben
    try:
        conv = Conversation()
        from extensions import db
        db.session.add(conv)
        db.session.commit()
        return conv
    except Exception:
        current_app.logger.exception("create conversation failed")
        abort(404, description="conversation create failed")


def _conversation_has_blob(conv_id: str, blob_id: int) -> bool:
    """
    Sicherheitscheck: Blob darf nur ausgeliefert werden, wenn er in einer Version
    der Konversation referenziert wurde.
    """
    try:
        from versioning import list_versions_by_conversation
        items = list_versions_by_conversation(conversation_id=conv_id, kind=None) or []
        for v in items:
            if int(v.get("input_blob_id") or 0) == int(blob_id):
                return True
    except Exception:
        pass
    return False


def _cache_headers(resp, strong: bool = False):
    """
    Einheitliche Cache-Header.
    In DEV: no-store. In PROD: kurzer Cache, bei strong langer Cache.
    """
    try:
        dev = str(current_app.config.get("FLASK_ENV", "")).lower().startswith("dev")
        if dev:
            resp.headers["Cache-Control"] = "no-store"
        else:
            if strong:
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                resp.headers["Cache-Control"] = "public, max-age=86400"
    except Exception:
        pass
    return resp


def _frame_headers(resp):
    """
    CSP / XFO analog zu chat.py.
    """
    try:
        if request.args.get("allow_embed") == "1":
            resp.headers["Content-Security-Policy"] = "frame-ancestors *"
            resp.headers.pop("X-Frame-Options", None)
        else:
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    except Exception:
        pass
    return resp


# ───────────────────────── CAD Service Helpers ─────────────────────────

def _cfg_cad_internal_base() -> str:
    """
    Interner Base-URL für Server-zu-Server Requests (Docker-Netz).
    Default: http://cad:8050
    """
    url = str(current_app.config.get("CADVIEWER_BASE_URL") or "").strip()
    return url or "http://cad:8050"


def _cfg_cad_public_base() -> str:
    """
    Öffentlicher Base-URL für Iframe (vom Browser erreichbar).
    Default: http://localhost:8050
    """
    url = str(current_app.config.get("CADVIEWER_PUBLIC_URL") or "").strip()
    return url or "http://localhost:8050"


def _http_get_json(url: str, timeout: float = 10.0) -> Tuple[bool, Any]:
    """
    GET JSON mit requests, fallback urllib.
    """
    try:
        import requests  # type: ignore
        r = requests.get(url, timeout=timeout)
        if r.ok:
            try:
                return True, r.json()
            except Exception:
                return False, None
        return False, None
    except Exception:
        try:
            from urllib.request import urlopen, Request  # type: ignore
            with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as resp:
                data = resp.read()
                try:
                    return True, json.loads(data.decode("utf-8"))
                except Exception:
                    return False, None
        except Exception:
            return False, None


def _http_post_file(url: str, field_name: str, filename: str, data: bytes, content_type: str = "application/dxf",
                    timeout: float = 30.0) -> Tuple[bool, Any]:
    """
    POST multipart file. Bevorzugt requests, fallback urllib (rudimentär).
    """
    try:
        import requests  # type: ignore
        files = {field_name: (filename, data, content_type)}
        r = requests.post(url, files=files, timeout=timeout)
        if r.ok:
            try:
                return True, r.json()
            except Exception:
                return True, None
        return False, getattr(r, "text", "")
    except Exception:
        # einfacher multipart Fallback
        try:
            boundary = "----cadFormBoundary7MA4YWxkTrZu0gW"
            body = []
            body.append(f"--{boundary}\r\n".encode())
            body.append(f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode())
            body.append(f"Content-Type: {content_type}\r\n\r\n".encode())
            body.append(data)
            body.append(f"\r\n--{boundary}--\r\n".encode())
            payload = b"".join(body)

            from urllib.request import Request, urlopen  # type: ignore
            req = Request(url, data=payload, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            req.add_header("Content-Length", str(len(payload)))
            with urlopen(req, timeout=timeout) as resp:
                out = resp.read()
                try:
                    return True, json.loads(out.decode("utf-8"))
                except Exception:
                    return True, None
        except Exception:
            return False, None


def _safe_name(s: str) -> str:
    """
    Dateinamen säubern: nur a-z0-9._- zulassen, Punkt nur einmal vor .dxf
    """
    try:
        base = os.path.basename(s or "plan.dxf")
        if not base.lower().endswith(".dxf"):
            base = base + ".dxf"
        base = re.sub(r"[^a-zA-Z0-9._-]+", "_", base)
        return base
    except Exception:
        return "plan.dxf"


def _build_cad_name(conv_id: str, label: str, rev: Any) -> str:
    try:
        suffix = f"_r{rev}" if str(rev) else ""
        name = f"{conv_id}{suffix}_{_safe_name(label)}"
        # doppelte .dxf beseitigen
        name = re.sub(r"\.dxf\.dxf$", ".dxf", name, flags=re.IGNORECASE)
        return name
    except Exception:
        return _safe_name(label or "plan.dxf")


def _load_blob_bytes(blob_id: object) -> Optional[bytes]:
    """
    Holt Bytes eines Blob-Eintrags, falls blob_id int-artig ist.
    Andernfalls None (der Aufrufer fällt auf URL zurück).
    """
    try:
        if blob_id is None:
            return None
        # "int-like" prüfen
        bid = int(str(blob_id))  # wirft bei UUID o.ä. eine Exception
    except Exception:
        return None

    try:
        b = Blob.query.get(bid)
        if not b or not b.data:
            return None
        return bytes(b.data) if isinstance(b.data, (bytes, bytearray)) else bytes(b.data)
    except Exception:
        return None


def _fetch_local_path_bytes(path: str) -> Optional[bytes]:
    """
    Optionaler Fallback: Wenn WEB_INTERNAL_URL gesetzt ist, kann Server sich
    die Datei selbst via HTTP holen (nur für lokale Pfade).
    """
    try:
        base = str(current_app.config.get("WEB_INTERNAL_URL") or "").strip()
        if not base:
            return None
        if not _is_local_path(path):
            return None
        url = base.rstrip("/") + path
        ok, data = False, None
        try:
            import requests  # type: ignore
            r = requests.get(url, timeout=15)
            if r.ok:
                return r.content
            return None
        except Exception:
            from urllib.request import urlopen  # type: ignore
            with urlopen(url, timeout=15) as resp:
                return resp.read()
    except Exception:
        return None


# ───────────────────────── pages ─────────────────────────

@bp.get("/ui/chat/<chat_id>/cad2d")
def cad2d_page(chat_id: str):
    """
    Iframe-Seite für den 2D-Viewer (alte Variante).
    Wird im neuen Flow nicht zwingend benötigt, bleibt aber kompatibel.
    """
    conv = _ensure_conv(chat_id)

    try:
        plan2d_url = url_for("ui_2dviewer.plan2d_json", chat_id=conv.id)
    except Exception:
        plan2d_url = f"/ui/chat/{conv.id}/plan2d.json"

    resp = make_response(render_template("viewer/cad2d.html",
                                         chat_id=conv.id,
                                         plan2d_url=plan2d_url))
    _cache_headers(resp, strong=False)
    _frame_headers(resp)
    return resp


@bp.get("/ui/chat/<chat_id>/plan2d.json")
def plan2d_json(chat_id: str):
    """
    Liefert Metadaten für den 2D-Viewer (DXF-Quelle).
    Wird weiterhin genutzt, u. a. um Raw-Links im UI zu zeigen.
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

    def _fallback_url() -> str:
        try:
            tmpl = current_app.config.get("PLAN2D_FALLBACK_URL_TEMPLATE", "")
            return tmpl.format(chat_id=conv.id) if tmpl else ""
        except Exception:
            return ""

    # VIEW_ONLY -> bevorzugt Fallback
    if view_only:
        u = _fallback_url()
        if u:
            dxf_url = u
            src = "config-template"
        else:
            info = _find_latest_dxf_version(conv.id)
            if info:
                label_local = info.get("label") or label
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
                label = label_local

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
        u = _fallback_url()
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

    data = BytesIO(blob.data if isinstance(blob.data, (bytes, bytearray)) else bytes(blob.data))
    resp = make_response(data.getvalue())
    resp.headers["Content-Type"] = blob.mime or "application/dxf"
    resp.headers["Content-Length"] = str(len(data.getvalue()))
    disp = f'inline; filename="{os.path.basename(blob.filename or "plan.dxf")}"'
    resp.headers["Content-Disposition"] = disp
    if getattr(blob, "sha256", None):
        resp.headers["ETag"] = blob.sha256
        _cache_headers(resp, strong=True)
    else:
        _cache_headers(resp, strong=False)
    _frame_headers(resp)
    return resp


# ───────────────────────── Neuer Endpoint: CAD-Embed ─────────────────────────

@bp.get("/ui/chat/<chat_id>/cad-embed.json")
def cad_embed_json(chat_id: str):
    """
    Liefert die Iframe-URL für den CAD-Microservice.
    Ablauf:
      1) jüngste DXF-Version bestimmen (oder 404)
      2) Ziel-Dateiname für den CAD-Service bilden
      3) prüfen, ob Datei im CAD-Service vorliegt (GET /api/v1/files)
      4) falls nicht, Datei hochladen (POST /api/v1/files, Feld 'file')
      5) öffentliche Embed-URL zurückgeben: <PUBLIC_BASE>/embed?file=<NAME>
    """
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404

    info = _find_latest_dxf_version(conv.id)
    if not info:
        # Optionaler Fallback auf PLAN2D_FALLBACK_URL_TEMPLATE
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

        # bevorzugt lokalen Blob streamen
        if not dxf_url and blob_id:
            try:
                dxf_url = url_for("ui_2dviewer.dxf_blob", chat_id=conv.id, blob_id=int(blob_id))
            except Exception:
                dxf_url = ""

    cad_base_internal = _cfg_cad_internal_base().rstrip("/")
    cad_base_public = _cfg_cad_public_base().rstrip("/")

    name = _build_cad_name(conv.id, label, rev)
    embed_url = f"{cad_base_public}/embed?file={quote(name)}"

    # 1) prüfen, ob bereits vorhanden
    ok, listing = _http_get_json(f"{cad_base_internal}/api/v1/files")
    if ok and isinstance(listing, list) and name in listing:
        payload = {
            "chat_id": conv.id,
            "status": "ok",
            "embed_url": embed_url,
            "dxf_url": dxf_url,
            "file": name,
            "rev": rev,
        }
        return jsonify(payload), 200

    # 2) Daten laden (Blob bevorzugt, sonst optional per interner HTTP-URL)
    data_bytes: Optional[bytes] = None
    if blob_id is not None:
        data_bytes = _load_blob_bytes(blob_id)

    if data_bytes is None:
        if _is_local_path(dxf_url):
            data_bytes = _fetch_local_path_bytes(dxf_url)

    if data_bytes is None:
        return jsonify({
            "chat_id": conv.id,
            "status": "error",
            "error": "DXF bytes not available for upload"
        }), 500

    # 3) Upload zum CAD-Service
    ok, resp = _http_post_file(f"{cad_base_internal}/api/v1/files", "file", name, data_bytes, "application/dxf")
    if not ok:
        return jsonify({
            "chat_id": conv.id,
            "status": "error",
            "error": "upload to CAD service failed"
        }), 502

    # 4) OK → Iframe-URL zurückgeben
    payload = {
        "chat_id": conv.id,
        "status": "ok",
        "embed_url": embed_url,
        "dxf_url": dxf_url,
        "file": name,
        "rev": rev,
    }
    return jsonify(payload), 200
