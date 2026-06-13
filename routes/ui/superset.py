# /services/app/routes/ui/superset.py
from __future__ import annotations

import posixpath
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote, urlsplit, urlunsplit

from flask import Blueprint, current_app, jsonify, make_response, request
from werkzeug.wrappers import Response

from models import Conversation


bp = Blueprint("ui_superset", __name__)

# ───────────────────────── internal cache (best-effort) ─────────────────────────

_BASE_CACHE: Dict[str, str] = {}  # keys: public_base, base_path, full_base


# ───────────────────────── helpers ─────────────────────────

def _is_dev() -> bool:
    try:
        env = str(current_app.config.get("FLASK_ENV", "") or "").lower()
        return env.startswith("dev") or env.startswith("development")
    except Exception:
        return False


def _cfg_str(key: str, default: str = "") -> str:
    try:
        v = current_app.config.get(key, default)
        return str(v) if v is not None else default
    except Exception:
        return default


def _norm_url(url: str, default: str = "") -> str:
    try:
        s = str(url or "").strip()
        if not s:
            return default
        return s.rstrip("/")
    except Exception:
        return default


def _norm_path(path: str, default: str = "/") -> str:
    try:
        s = str(path or "").strip()
        if not s:
            return default
        if not s.startswith("/"):
            s = "/" + s
        while "//" in s:
            s = s.replace("//", "/")
        return s
    except Exception:
        return default


def _apply_security_headers(resp: Response) -> None:
    try:
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
    except Exception:
        pass
    try:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    except Exception:
        pass


def _apply_cache_headers(resp: Response, *, no_store: bool = False) -> None:
    """
    DEV: no-store
    PROD: i.d.R. no-store (Session/Redirects sollen nicht gecacht werden)
    """
    try:
        if no_store or _is_dev():
            resp.headers["Cache-Control"] = "no-store"
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
    except Exception:
        pass


def _apply_frame_headers(resp: Response) -> None:
    """
    Standard: SAMEORIGIN.
    Optional per ?allow_embed=1: frame-ancestors * und XFO entfernen.
    """
    try:
        if request.args.get("allow_embed") == "1":
            resp.headers["Content-Security-Policy"] = "frame-ancestors *"
            try:
                resp.headers.pop("X-Frame-Options", None)
            except Exception:
                pass
        else:
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    except Exception:
        pass


def _finalize_html_like(resp: Response, *, no_store: bool = True) -> None:
    try:
        _apply_security_headers(resp)
    except Exception:
        pass
    try:
        _apply_cache_headers(resp, no_store=no_store)
    except Exception:
        pass
    try:
        _apply_frame_headers(resp)
    except Exception:
        pass


def _finalize_json(resp: Response, *, no_store: bool = True) -> None:
    try:
        _apply_security_headers(resp)
    except Exception:
        pass
    try:
        _apply_cache_headers(resp, no_store=no_store)
    except Exception:
        pass


def _simple_html_error(message: str, code: int = 500) -> Response:
    try:
        safe = (message or "error")[:1200]
    except Exception:
        safe = "error"
    html = (
        "<!doctype html><html lang='de'><head>"
        "<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{code}</title></head>"
        "<body style='font:14px/1.4 system-ui,Segoe UI,Roboto,Arial;margin:24px;'>"
        f"<h1 style='margin:0 0 10px 0;'>{code}</h1>"
        f"<p style='margin:0;color:#444;'>{safe}</p>"
        "</body></html>"
    )
    resp = make_response(html, code)
    try:
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
    except Exception:
        pass
    _finalize_html_like(resp, no_store=True)
    return resp


def _ensure_conversation(chat_id: str) -> Optional[Conversation]:
    """
    Superset ist nicht chat-abhängig, aber wir halten das UI-Namespace konsistent.
    Route liefert 404 im Iframe-freundlichen HTML-Format, falls chat_id ungültig.
    """
    try:
        if not chat_id:
            return None
        return Conversation.query.get(str(chat_id))
    except Exception:
        return None


def _safe_rel_path(raw: str) -> str:
    """
    Sanitized relative path (ohne führendes '/'):
      - blockt absolute URLs, //, schemata
      - normalisiert .. und .
    """
    try:
        s = (raw or "").strip()
        if not s:
            return ""
        try:
            s = unquote(s)
        except Exception:
            pass

        low = s.lower()
        if "://" in low or low.startswith("//"):
            return ""

        s = s.replace("\\", "/")
        s = s.lstrip("/")

        s = posixpath.normpath("/" + s).lstrip("/")

        if s in {"", ".", "/"}:
            return ""
        return s
    except Exception:
        return ""


def _build_superset_base() -> str:
    """
    Cached: public base + base path.
    Beispiele:
      SUPERSET_PUBLIC_URL=http://localhost:8088
      SUPERSET_BASE_PATH=/
      -> http://localhost:8088/
    """
    try:
        public_base = _norm_url(_cfg_str("SUPERSET_PUBLIC_URL", "http://localhost:8088"), "http://localhost:8088")
        base_path = _norm_path(_cfg_str("SUPERSET_BASE_PATH", "/"), "/")

        ck = f"{public_base}|{base_path}"
        cached_key = _BASE_CACHE.get("key", "")
        if cached_key == ck:
            v = _BASE_CACHE.get("full_base", "")
            if v:
                return v

        full = public_base + base_path
        _BASE_CACHE["key"] = ck
        _BASE_CACHE["public_base"] = public_base
        _BASE_CACHE["base_path"] = base_path
        _BASE_CACHE["full_base"] = full
        return full
    except Exception:
        return "http://localhost:8088/"


def _join_url(base: str, rel: str) -> str:
    """
    Fügt rel (relative path, ohne führenden Slash) an base an,
    ohne Query/Fragment zu verändern.
    """
    try:
        b = str(base or "").strip()
        if not b:
            return ""
        parts = urlsplit(b)
        base_path = parts.path or "/"
        if not base_path.endswith("/"):
            base_path += "/"
        if rel:
            new_path = posixpath.normpath(base_path + rel)
            if not new_path.startswith("/"):
                new_path = "/" + new_path
        else:
            new_path = base_path
        return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))
    except Exception:
        return base


def _build_target_url() -> Tuple[str, Dict[str, Any]]:
    """
    Liefert (target_url, meta).
    Unterstützt optional:
      ?path=/some/where  oder  ?p=/some/where
    """
    meta: Dict[str, Any] = {"service": "superset", "source": "config"}

    base = _build_superset_base()
    if not base:
        meta["error"] = "SUPERSET_PUBLIC_URL not configured"
        return "", meta

    raw_path = request.args.get("path") or request.args.get("p") or ""
    rel = _safe_rel_path(raw_path)
    if raw_path and not rel:
        meta["path_status"] = "ignored"
        meta["path_raw"] = raw_path
    elif rel:
        meta["path_status"] = "ok"
        meta["path_rel"] = rel
    else:
        meta["path_status"] = "empty"

    target = _join_url(base, rel)
    return target, meta


# ───────────────────────── routes ─────────────────────────

@bp.get("/ui/chat/<chat_id>/superset")
def superset_page(chat_id: str):
    """
    Iframe-Ziel für Admin → Data-Analyse.
    Minimal-Variante (Start):
      - validiert chat_id (Konversation existiert)
      - redirect (302) auf SUPERSET_PUBLIC_URL (+ optionaler Unterpfad)
    Später erweiterbar auf Proxy-Modus ohne Frontend-Anpassungen.
    """
    conv = _ensure_conversation(chat_id)
    if not conv:
        return _simple_html_error("chat not found", 404)

    target, meta = _build_target_url()
    if not target:
        return _simple_html_error(meta.get("error") or "superset target not configured", 500)

    # optional: direct=0 liefert eine kleine HTML-Seite statt Redirect (Debug/Fallback)
    if request.args.get("direct") == "0":
        open_label = "Superset öffnen"
        html = (
            "<!doctype html><html lang='de'><head>"
            "<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Superset</title></head>"
            "<body style='margin:0;font:13px/1.4 system-ui,Segoe UI,Roboto,Arial;'>"
            "<div style='padding:10px;border-bottom:1px solid #e5e7eb;background:#fff'>"
            f"<a href='{target}' target='_blank' rel='noopener' style='text-decoration:none'>{open_label}</a>"
            "</div>"
            f"<iframe src='{target}' style='border:0;width:100%;height:calc(100vh - 44px);display:block'></iframe>"
            "</body></html>"
        )
        resp = make_response(html, 200)
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
        _finalize_html_like(resp, no_store=True)
        return resp

    resp = make_response("", 302)
    resp.headers["Location"] = target
    _finalize_html_like(resp, no_store=True)
    return resp


@bp.get("/ui/chat/<chat_id>/superset.json")
def superset_json(chat_id: str):
    """
    Debug/Frontend-Hilfe: liefert die berechnete Ziel-URL.
    """
    conv = _ensure_conversation(chat_id)
    if not conv:
        resp = jsonify({"error": "not found"})
        _finalize_json(resp, no_store=True)
        return resp, 404

    target, meta = _build_target_url()
    payload = {
        "chat_id": conv.id,
        "service": "superset",
        "target_url": target,
        "meta": meta,
    }
    resp = jsonify(payload)
    _finalize_json(resp, no_store=True)
    return resp, 200