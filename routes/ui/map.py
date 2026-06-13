from __future__ import annotations

import base64
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urljoin

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    make_response,
    request,
)
from werkzeug.wrappers import Response

from extensions import db
from models import Conversation

bp = Blueprint("ui_map", __name__)

_STYLE_RE = re.compile(r"^[a-z0-9\-]+/[a-z0-9\-\.]+$", re.IGNORECASE)


# ───────────────────────── helpers ─────────────────────────

def _cfg_str(key: str, default: str = "") -> str:
    try:
        val = current_app.config.get(key, default)
        return str(val) if val is not None else default
    except Exception:
        return default


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(current_app.config.get(key, default))
    except Exception:
        return default


def _cfg_bool(key: str, default: bool = False) -> bool:
    try:
        val = current_app.config.get(key, default)
        if isinstance(val, bool):
            return val
        s = str(val).strip().lower()
        if s in {"1", "true", "yes", "y", "on"}:
            return True
        if s in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _cfg_list(key: str, default: List[str]) -> List[str]:
    try:
        val = current_app.config.get(key, default)
        if isinstance(val, (list, tuple)):
            return [str(x) for x in val]
        if isinstance(val, str):
            return [s.strip() for s in val.split(",") if s.strip()]
        return list(default)
    except Exception:
        return list(default)


def _cache_headers(resp: Response, *, strong: bool = False) -> Response:
    try:
        dev = str(current_app.config.get("FLASK_ENV", "")).lower().startswith("dev")
        if dev:
            resp.headers["Cache-Control"] = "no-store"
        else:
            if strong:
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                resp.headers["Cache-Control"] = "public, max-age=120"
    except Exception:
        pass
    return resp


def _frame_headers(resp: Response) -> Response:
    """
    Erlaubt optional das Einbetten (iframe), wenn ?allow_embed=1, ansonsten SAMEORIGIN.
    """
    try:
        if request.args.get("allow_embed") == "1":
            resp.headers["Content-Security-Policy"] = "frame-ancestors *"
            try:
                del resp.headers["X-Frame-Options"]
            except Exception:
                pass
        else:
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    except Exception:
        pass
    return resp


def _ensure_conversation(chat_id: Optional[str]) -> Conversation:
    try:
        if chat_id:
            c = Conversation.query.get(str(chat_id))
            if c:
                return c
        c = Conversation()
        db.session.add(c)
        db.session.commit()
        return c
    except Exception:
        current_app.logger.exception("create conversation failed")
        abort(404, description="conversation not available")


def _wfs_base() -> str:
    """
    Externe GeoServer Basis-URL (ohne /wfs). Beispiel: https://geo.vectoplan.com/geoserver
    """
    try:
        base = _cfg_str("GEOSERVER_WFS_BASE", "").rstrip("/")
        return base or ""
    except Exception:
        return ""


def _wfs_auth_header() -> Dict[str, str]:
    """
    Basic-Auth Header aus Config (Server-zu-Server). Kein Leak ins Frontend.
    """
    try:
        user = _cfg_str("GEOSERVER_WFS_USER", "")
        pw = _cfg_str("GEOSERVER_WFS_PASSWORD", "")
        if not user or not pw:
            return {}
        token = base64.b64encode(f"{user}:{pw}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}
    except Exception:
        return {}


def _is_typename_allowed(name: str, allow: List[str]) -> bool:
    """
    Erlaubt nur konfiguriert freigegebene Layernamen/Namespaces.
    """
    try:
        s = (name or "").strip()
        if not s:
            return False
        if s in allow:
            return True
        for pref in allow:
            if pref and s.startswith(pref):
                return True
        return False
    except Exception:
        return False


def _sanitize_typenames(raw: str, allow: List[str]) -> str:
    """
    Filtert eine CSV-Liste typeNames=... gegen Allow-List.
    """
    try:
        parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
        safe = [p for p in parts if _is_typename_allowed(p, allow)]
        return ",".join(safe)
    except Exception:
        return ""


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except Exception:
        return minimum


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except Exception:
        return minimum


def _get_default_center() -> List[float]:
    try:
        center = current_app.config.get("MAP_DEFAULT_CENTER", [11.576124, 48.137154]) or [11.576124, 48.137154]
        if isinstance(center, (list, tuple)) and len(center) >= 2:
            lon = _clamp_float(float(center[0]), -180.0, 180.0)
            lat = _clamp_float(float(center[1]), -90.0, 90.0)
            return [lon, lat]
    except Exception:
        pass
    return [11.576124, 48.137154]


def _parse_lon_lat_zoom() -> tuple[float, float, int]:
    center = _get_default_center()
    default_lon = center[0]
    default_lat = center[1]
    default_zoom = _cfg_int("MAP_DEFAULT_ZOOM", 14)

    try:
        lon = _clamp_float(float(request.args.get("lon", default_lon)), -180.0, 180.0)
    except Exception:
        lon = default_lon

    try:
        lat = _clamp_float(float(request.args.get("lat", default_lat)), -90.0, 90.0)
    except Exception:
        lat = default_lat

    try:
        zoom = _clamp_int(int(request.args.get("zoom", default_zoom)), 0, 22)
    except Exception:
        zoom = _clamp_int(default_zoom, 0, 22)

    return lon, lat, zoom


def _sanitize_style(style: Optional[str]) -> str:
    try:
        value = (style or "").strip()
        if not value:
            return ""
        return value if _STYLE_RE.match(value) else ""
    except Exception:
        return ""


def _normalize_scroll_query(raw: Optional[str]) -> Optional[str]:
    """
    Zielsemantik für OpenLayer-Service:
    - scroll=1 / true  -> Mausrad-Zoom an
    - scroll=0 / false -> Mausrad-Zoom aus

    Für die Einbettung soll standardmäßig Scrollen aktiv sein.
    """
    if raw is None:
        return None

    try:
        value = str(raw).strip().lower()
    except Exception:
        return None

    if value in {"1", "true", "yes", "y", "on"}:
        return "1"
    if value in {"0", "false", "no", "n", "off"}:
        return "0"
    return None


def _build_openlayer_query() -> Dict[str, Any]:
    """
    Baut die Redirect-Query für den OpenLayer-Service.

    Wichtige Entscheidung:
    - style wird NUR weitergegeben, wenn er explizit im Request gesetzt ist.
      Sonst verwendet der OpenLayer-Service seinen eigenen Default-Stil
      aus services/OpenLayer/.env.
    - scroll ist standardmäßig "1", damit Mouse-Wheel-Zoom im Iframe aktiv ist.
    """
    lon, lat, zoom = _parse_lon_lat_zoom()

    explicit_style = _sanitize_style(request.args.get("style"))
    explicit_scroll = _normalize_scroll_query(request.args.get("scroll"))

    query: Dict[str, Any] = {
        "lon": lon,
        "lat": lat,
        "zoom": zoom,
        # Standard jetzt bewusst aktiv:
        # Wenn der Parent nichts vorgibt, soll im Iframe gezoomt werden können.
        "scroll": explicit_scroll if explicit_scroll is not None else "1",
    }

    if explicit_style:
        query["style"] = explicit_style

    cache_buster = request.args.get("r")
    if cache_buster not in (None, ""):
        query["r"] = cache_buster

    return query


# ───────────────────────── pages ─────────────────────────

@bp.get("/ui/chat/<chat_id>/map")
def map_page(chat_id: str):
    """
    Iframe-Ziel: leitet direkt auf den OpenLayer-Microservice um.
    Keine Doppel-Frames.

    Verhalten:
    - lon/lat/zoom werden sicher geparst und begrenzt
    - style wird nur weitergegeben, wenn explizit angefragt
    - dadurch kann der OpenLayer-Service seinen eigenen Satelliten-Default
      aus .env verwenden
    - scroll ist standardmäßig aktiv, damit Mausrad-Zoom im Iframe funktioniert
    """
    _ = _ensure_conversation(chat_id)

    try:
        base = _cfg_str("OPENLAYER_PUBLIC_URL", "http://localhost:8090").rstrip("/")
        query = _build_openlayer_query()

        target = f"{base}/map?{urlencode(query)}"

        resp = make_response("", 302)
        resp.headers["Location"] = target

        # Iframe-Embedding explizit erlauben
        resp.headers["Content-Security-Policy"] = "frame-ancestors *"
        try:
            del resp.headers["X-Frame-Options"]
        except Exception:
            pass

        return resp
    except Exception as ex:
        current_app.logger.exception("map_page redirect failed")
        return jsonify({"error": str(ex)}), 500


@bp.get("/ui/chat/<chat_id>/map.json")
def map_json(chat_id: str):
    """
    Liefert Map-Konfig (keine Secrets): min_zoom, Start-View, WFS-Endpoint (Server-Proxy),
    erlaubte Layernamen/Namespaces, Standard-Projectionen.
    """
    conv = _ensure_conversation(chat_id)

    try:
        min_zoom = _cfg_int("MAP_MIN_ZOOM", 14)
        zoom = _cfg_int("MAP_DEFAULT_ZOOM", 6)
        center = _get_default_center()

        allowed = _cfg_list("WFS_ALLOWED_TYPENAMES", [
            "de_flurstueck:",
            "de_flurstueck:fluerstuck_",
            "de_flurstueck:flurstueck_",
        ])

        payload = {
            "chat_id": conv.id,
            "view": {
                "center": center,
                "zoom": zoom
            },
            "min_zoom": min_zoom,
            "iframe_defaults": {
                "mouse_wheel_zoom_enabled": True,
                "style_delegated_to_openlayer_service": True,
            },
            "wfs": {
                "proxy_url": f"/ui/chat/{conv.id}/wfs",
                "allowed_typeNames": allowed,
                "srsName": "EPSG:25833",
                "featureProjection": "EPSG:3857"
            }
        }
        resp = make_response(jsonify(payload), 200)
        _cache_headers(resp, strong=False)
        return resp
    except Exception as ex:
        current_app.logger.exception("map_json failed")
        return jsonify({"error": str(ex)}), 500


@bp.get("/ui/chat/<chat_id>/wfs")
def wfs_proxy(chat_id: str):
    """
    Sicherer Proxy zu GeoServer WFS:
    - Nur GET
    - Whitelist bekannter Query-Parameter
    - typeNames gegen Allowlist prüfen
    - Auth aus Server-Config (Basic)
    """
    _ = _ensure_conversation(chat_id)

    base = _wfs_base()
    if not base:
        return jsonify({"error": "WFS base not configured"}), 500

    try:
        allow_params = {
            "service", "version", "request",
            "typeNames", "typeName",
            "srsName", "bbox", "outputFormat",
            "maxFeatures", "startIndex",
            "propertyName", "cql_filter", "filter", "count",
        }

        params_in: Dict[str, str] = {}
        for k, v in request.args.items():
            try:
                if k in allow_params and v is not None:
                    params_in[k] = v
            except Exception:
                continue

        params_in.setdefault("service", "WFS")
        params_in.setdefault("version", "1.0.0")
        params_in.setdefault("request", "GetFeature")
        params_in.setdefault("outputFormat", "application/json")

        allowed_types = _cfg_list("WFS_ALLOWED_TYPENAMES", [])
        tn_raw = params_in.get("typeNames") or params_in.get("typeName") or ""
        tn_safe = _sanitize_typenames(tn_raw, allowed_types)
        if not tn_safe:
            return jsonify({"error": "typeNames not allowed or empty"}), 400

        params_in["typeNames"] = tn_safe
        params_in.pop("typeName", None)

        if "srsName" not in params_in:
            params_in["srsName"] = _cfg_str("WFS_DEFAULT_SRS", "EPSG:25833")

        try:
            wfs_url = urljoin(base + "/", "wfs")
        except Exception:
            wfs_url = base.rstrip("/") + "/wfs"

        query = urlencode(params_in, doseq=True)
        url = f"{wfs_url}?{query}"

        import requests  # type: ignore

        headers = {"Accept": "application/json"}
        headers.update(_wfs_auth_header())
        timeout = float(current_app.config.get("WFS_PROXY_TIMEOUT", 20.0))
        r = requests.get(url, headers=headers, timeout=timeout)

        ct = r.headers.get("Content-Type", "application/json")
        resp = make_response(r.content, r.status_code)
        resp.headers["Content-Type"] = ct
        _cache_headers(resp, strong=False)
        return resp

    except Exception as ex:
        current_app.logger.exception("wfs_proxy failed")
        return jsonify({"error": str(ex)}), 500