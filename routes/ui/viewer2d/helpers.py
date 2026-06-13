# /services/app/routes/ui/viewer2d/helpers.py
from __future__ import annotations

import os
import re
import json
from typing import Any, Dict, Optional, List, Tuple

from flask import current_app, request, abort
from models import Conversation, Blob

# ───────────────────────── Konstanten ─────────────────────────

_DXF_KINDS = {"BPA_DXF", "BPA_PLAN_DXF", "BPA_PLAN", "DXF_UPLOAD"}

# ───────────────────────── Utilities ─────────────────────────

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
    Holt oder erzeugt eine Conversation.
    """
    conv = Conversation.query.get(chat_id) if chat_id else None
    if conv is not None:
        return conv
    try:
        from extensions import db
        conv = Conversation()
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
