# /services/app/config.py
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional


# ───────────────────── Env (best-effort cache) ─────────────────────

try:
    _ENV_CACHE: Dict[str, str] = dict(os.environ)
except Exception:
    _ENV_CACHE = {}


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Best-effort env getter:
      - nutzt einen Snapshot von os.environ (Cache)
      - fallback auf os.getenv()
    """
    try:
        if key in _ENV_CACHE:
            v = _ENV_CACHE.get(key)
            return v if v is not None else default
        return os.getenv(key, default)
    except Exception:
        return default


def _env_str(key: str, default: str = "") -> str:
    try:
        v = _env(key, None)
        return str(v) if v is not None else default
    except Exception:
        return default


# ───────────────────── Helper ─────────────────────

_STYLE_RE = re.compile(r"^[a-z0-9\-]+/[a-z0-9\-\.]+$", re.IGNORECASE)


def _as_bool(val: Optional[str], default: bool = False) -> bool:
    try:
        if val is None:
            return default
        v = str(val).strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _as_int(val: Optional[str], default: int) -> int:
    try:
        return int(str(val).strip())
    except Exception:
        return default


def _as_float(val: Optional[str], default: float) -> float:
    try:
        return float(str(val).strip())
    except Exception:
        return default


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except Exception:
        return minimum


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except Exception:
        return minimum


def _as_json_list(val: Optional[str]) -> List[Any]:
    """
    Robust:
      - akzeptiert JSON-Array-Strings, z. B. '["11.5","48.1"]'
      - fallback: CSV '11.5,48.1' -> ["11.5","48.1"]
    """
    try:
        if val is None:
            return []
        s = str(val).strip()
        if not s:
            return []
        try:
            data = json.loads(s)
            return data if isinstance(data, list) else []
        except Exception:
            pass
        if "," in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        return []
    except Exception:
        return []


def _norm_url(url: str, default: str = "") -> str:
    """
    Entfernt trailing '/', lässt Schema/Host unangetastet.
    """
    try:
        s = str(url or "").strip()
        if not s:
            return default
        return s.rstrip("/")
    except Exception:
        return default


def _norm_path(path: str, default: str = "/") -> str:
    """
    Normalisiert Pfade:
      - stellt führendes '/' sicher
      - entfernt doppelte Slashes
      - liefert mindestens default (z. B. '/')
    """
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


def _sanitize_style_id(style_id: str, default: str) -> str:
    """
    Akzeptiert nur Mapbox-Style-IDs im Format '<owner>/<style-id>'.
    """
    try:
        s = str(style_id or "").strip()
        if not s:
            return default
        return s if _STYLE_RE.match(s) else default
    except Exception:
        return default


def _as_center_pair(val: Optional[str], default_lon: float, default_lat: float) -> List[float]:
    """
    MAP_DEFAULT_CENTER:
      - JSON-Liste oder CSV
      - Ergebnis immer [lon, lat] in WGS84
      - Werte werden sicher geclamped
    """
    try:
        raw = _as_json_list(val)
        lon = float(raw[0]) if len(raw) > 0 else default_lon
        lat = float(raw[1]) if len(raw) > 1 else default_lat
        return [
            _clamp_float(lon, -180.0, 180.0),
            _clamp_float(lat, -90.0, 90.0),
        ]
    except Exception:
        return [default_lon, default_lat]


# ───────────────────── Config ─────────────────────

class Config:
    # Flask / Core
    SECRET_KEY = _env_str("SECRET_KEY", "dev")
    SQLALCHEMY_DATABASE_URI = _env("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    FLASK_ENV = _env_str("FLASK_ENV", _env_str("ENV", "prod"))

    # Server
    PORT = _as_int(_env("PORT"), 8000)
    MEDIA_ROOT = _env_str("MEDIA_ROOT", "/app/media")
    MAX_CONTENT_LENGTH = _as_int(_env("MAX_CONTENT_LENGTH"), 1024 * 1024 * 512)  # 512 MB

    # Interne Services
    CHATAI_URL = _env_str("CHATAI_URL", "http://chatai:8001/chat")
    FPA_URL = _env_str("FPA_URL", "http://fpa:8080")
    DAA_URL = _env_str("DAA_URL", "http://daa:8000")
    BGA_URL = _env_str("BGA_URL", "http://bga:4903")
    BPA_URL = _env_str("BPA_URL", "http://bpa:5744")
    CITY_URL = _env_str("CITY_URL", "http://cityloader:8000")
    DATALOADER_URL = _env("DATALOADER_URL")  # optional

    # Interner Basislink dieses Web-Services (Server→Server)
    WEB_INTERNAL_URL = _env_str("WEB_INTERNAL_URL", "http://web:8000")

    # CAD-Viewer Microservice
    CADVIEWER_BASE_URL = _norm_url(_env_str("CADVIEWER_BASE_URL", "http://cad:8050"), "http://cad:8050")
    CADVIEWER_PUBLIC_URL = _norm_url(_env_str("CADVIEWER_PUBLIC_URL", "http://localhost:8050"), "http://localhost:8050")
    CADVIEWER_TIMEOUT = _as_int(_env("CADVIEWER_TIMEOUT"), 20)
    CADVIEWER_UPLOAD_FIELD = _env_str("CADVIEWER_UPLOAD_FIELD", "file")

    # Vectoplan / Speckle
    VECTOPLAN_HOST = _env_str("VECTOPLAN_HOST", "https://vectoplan.com")
    VECTOPLAN_TOKEN = _env("VECTOPLAN_TOKEN")
    VECTOPLAN_EMBED_TOKEN = _env("VECTOPLAN_EMBED_TOKEN")
    SPECKLE_UPLOAD_TIMEOUT = _as_int(_env("SPECKLE_UPLOAD_TIMEOUT"), 300)
    KEEP_VERSIONS_PER_PROJECT = _as_int(_env("KEEP_VERSIONS_PER_PROJECT"), 10)

    # Dateien/Caching
    FILE_CACHE_MAX_AGE = _as_int(_env("FILE_CACHE_MAX_AGE"), 3600)
    FILE_CONTENT_CACHE_MAX_AGE = _as_int(_env("FILE_CONTENT_CACHE_MAX_AGE"), 3600)

    # Chat/Uploads
    AUTO_UPLOAD_ATTACHMENTS = _as_bool(_env("AUTO_UPLOAD_ATTACHMENTS"), True)

    # Templates / Cards / State
    ENABLE_TEMPLATE_API = _as_bool(_env("ENABLE_TEMPLATE_API"), True)
    TEMPLATE_SEED_PATH = _env("TEMPLATE_SEED_PATH")
    _TEMPLATE_SEED_JSON = _env("TEMPLATE_SEED_JSON")
    TEMPLATE_SEED = _as_json_list(_TEMPLATE_SEED_JSON)
    TEMPLATE_IMPORT_TO_DB_ON_STARTUP = _as_bool(_env("TEMPLATE_IMPORT_TO_DB_ON_STARTUP"), False)

    # Projekt-Startkarte
    PROJECT_WELCOME_WFS_URL = _env_str("PROJECT_WELCOME_WFS_URL", "")
    PROJECT_WELCOME_LAYER = _env_str("PROJECT_WELCOME_LAYER", "")
    PROJECT_WELCOME_HINT = _env_str(
        "PROJECT_WELCOME_HINT",
        "Dies ist eine offene Alpha-Testversion von Vectoplan. Alle Systeme können kostenlos genutzt werden. Hier bauen testen wir neue System zur BigData-Auswertung und automatischen Gebäudegenerierung",
    )

    # 2D-Viewer Fallback
    PLAN2D_FALLBACK_URL_TEMPLATE = _env_str("PLAN2D_FALLBACK_URL_TEMPLATE", "/static/test/plan.dxf")

    # Strikte Anzeige ohne Uploads/CDN
    VIEW_ONLY_MODE = _as_bool(_env("VIEW_ONLY_MODE"), True)
    DISABLE_UI_UPLOADS = _as_bool(_env("DISABLE_UI_UPLOADS"), True)
    DISABLE_API_UPLOADS = _as_bool(_env("DISABLE_API_UPLOADS"), True)
    ALLOW_CDN = _as_bool(_env("ALLOW_CDN"), False)

    # Logging
    LOG_LEVEL = _env_str("LOG_LEVEL", "INFO")

    # ───────── OpenLayer-Microservice (Iframe) ─────────
    # Öffentliche Basis-URL (vom Browser erreichbar)
    OPENLAYER_PUBLIC_URL = _norm_url(
        _env_str("OPENLAYER_PUBLIC_URL", "http://localhost:8090"),
        "http://localhost:8090",
    )

    # Kartenmitte / Startzoom für Redirect und begleitende JSON-Endpunkte
    MAP_DEFAULT_CENTER = _as_center_pair(
        _env("MAP_DEFAULT_CENTER"),
        11.576124,
        48.137154,
    )
    MAP_DEFAULT_ZOOM = _clamp_int(_as_int(_env("MAP_DEFAULT_ZOOM"), 14), 0, 22)
    MAP_MIN_ZOOM = _clamp_int(_as_int(_env("MAP_MIN_ZOOM"), 0), 0, 22)
    MAP_MAX_ZOOM = _clamp_int(_as_int(_env("MAP_MAX_ZOOM"), 22), MAP_MIN_ZOOM, 22)

    # Scroll-/Wheel-Zoom:
    # Primär wird MAP_MOUSE_WHEEL_ZOOM ausgewertet.
    # Fallback aus Kompatibilitätsgründen: invertiertes MAP_DISABLE_SCROLL.
    _MAP_DISABLE_SCROLL_LEGACY = _as_bool(_env("MAP_DISABLE_SCROLL"), False)
    MAP_MOUSE_WHEEL_ZOOM = _as_bool(_env("MAP_MOUSE_WHEEL_ZOOM"), not _MAP_DISABLE_SCROLL_LEGACY)
    MAP_DISABLE_SCROLL = not MAP_MOUSE_WHEEL_ZOOM

    # Stil-Override:
    # Wichtig: Der ui_map-Redirect hängt standardmäßig KEIN style mehr an die
    # OpenLayer-URL, damit der OpenLayer-Service seinen eigenen Default aus
    # services/OpenLayer/.env nutzen kann.
    #
    # MAP_STYLE_ID bleibt trotzdem erhalten:
    # - für explizite Overrides
    # - für künftige UI-Konfigurationsfälle
    # - zur Dokumentation des gewünschten Mapbox-Stils im Web-Service
    #
    # Empfohlener Default:
    # - mapbox/satellite-streets-v12
    MAP_STYLE_ID = _sanitize_style_id(
        _env_str("MAP_STYLE_ID", "mapbox/satellite-streets-v12"),
        "mapbox/satellite-streets-v12",
    )

    # Optionaler Schalter für zukünftige Logik:
    # Falls ein expliziter Style-Override vom Web-Service erzwungen werden soll,
    # kann die Route das später über diesen Flag berücksichtigen.
    MAP_FORWARD_STYLE_TO_IFRAME = _as_bool(_env("MAP_FORWARD_STYLE_TO_IFRAME"), False)

    # Standardverhalten für den Iframe:
    # "1" = Wheel-Zoom aktiv
    # "0" = Wheel-Zoom deaktiviert
    MAP_IFRAME_SCROLL_DEFAULT = "1" if MAP_MOUSE_WHEEL_ZOOM else "0"

    # ───────── Crawlab (Admin → DataMining iframe) ─────────
    # Public: vom Browser erreichbar (z. B. über Port-Mapping)
    # Internal: im Docker-Netz erreichbar (z. B. Service-Name)
    CRAWLAB_PUBLIC_URL = _norm_url(
        _env_str("CRAWLAB_PUBLIC_URL", "http://localhost:8080"),
        "http://localhost:8080",
    )
    CRAWLAB_INTERNAL_URL = _norm_url(
        _env_str("CRAWLAB_INTERNAL_URL", "http://crawlab:8080"),
        "http://crawlab:8080",
    )
    CRAWLAB_BASE_PATH = _norm_path(_env_str("CRAWLAB_BASE_PATH", "/"), "/")

    # ───────── Superset (Admin → Data-Analyse iframe) ─────────
    SUPERSET_PUBLIC_URL = _norm_url(
        _env_str("SUPERSET_PUBLIC_URL", "http://localhost:8088"),
        "http://localhost:8088",
    )
    SUPERSET_INTERNAL_URL = _norm_url(
        _env_str("SUPERSET_INTERNAL_URL", "http://superset:8088"),
        "http://superset:8088",
    )
    SUPERSET_BASE_PATH = _norm_path(_env_str("SUPERSET_BASE_PATH", "/"), "/")


__all__ = ["Config"]