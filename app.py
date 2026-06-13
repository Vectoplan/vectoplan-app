# /services/app/app.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from flask import Flask, jsonify, Blueprint, request, current_app
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import db, init_logging

# Blueprints
from routes.ui.chat import bp as ui_chat_bp
from routes.ui.viewer2d import bp as ui_2dviewer_bp
from routes.chat import bp as chat_api_bp
from routes.files import bp as files_bp
from routes.embed import bp as embed_bp
from routes.speckle_upload import bp as speckle_upload_bp
from routes.ui.map import bp as ui_map_bp
from routes.vectoplan_ingest import bp as vectoplan_ingest_bp
from routes.viewer_selection import bp as viewer_selection_bp
from routes.blobs_base64 import bp as blobs_base64_bp  # NEU
from routes.versions_api import bp as versions_api_bp   # NEU
from routes.vectoplan_align import bp as vectoplan_align_bp


def _try_import_optional_blueprints() -> Dict[str, Optional[Blueprint]]:
    out: Dict[str, Optional[Blueprint]] = {"templates_api_bp": None, "state_api_bp": None}
    try:
        from routes.templates import bp as templates_api_bp  # type: ignore
        out["templates_api_bp"] = templates_api_bp
    except Exception:
        out["templates_api_bp"] = None
    try:
        from routes.state import bp as state_api_bp  # type: ignore
        out["state_api_bp"] = state_api_bp
    except Exception:
        out["state_api_bp"] = None
    return out


def _try_import_ui_integrations() -> Dict[str, Optional[Blueprint]]:
    """
    Optional-Import, damit das Projekt auch startet, falls einzelne UI-Integrationen
    (noch) nicht vorhanden sind oder Importfehler haben.
    """
    out: Dict[str, Optional[Blueprint]] = {"ui_crawlab_bp": None, "ui_superset_bp": None}
    try:
        from routes.ui.crawlab import bp as ui_crawlab_bp  # type: ignore
        out["ui_crawlab_bp"] = ui_crawlab_bp
    except Exception:
        out["ui_crawlab_bp"] = None
    try:
        from routes.ui.superset import bp as ui_superset_bp  # type: ignore
        out["ui_superset_bp"] = ui_superset_bp
    except Exception:
        out["ui_superset_bp"] = None
    return out


def _seed_templates_if_configured(app: Flask) -> None:
    """Seeds in Memory laden; optional in DB materialisieren."""
    try:
        # Defaults für Seeds bereitstellen, falls leer
        try:
            from seed_templates import wire_app_defaults  # type: ignore
            wire_app_defaults(app)
        except Exception:
            pass

        import messages as _msg  # type: ignore

        try:
            # mehrfaches Laden explizit erlauben
            setattr(_msg._ensure_seed_loaded, "_done", False)
        except Exception:
            pass
        _msg._ensure_seed_loaded()

        if bool(app.config.get("TEMPLATE_IMPORT_TO_DB_ON_STARTUP", False)):
            items = _msg.list_templates() or []
            for t in items:
                try:
                    _msg.register_template(
                        key=str(t.get("key") or ""),
                        schema_json=t.get("schema_json") or {},
                        renderer=str(t.get("renderer") or "InfoCard"),
                        title=str(t.get("title") or t.get("key") or ""),
                        version=int(t.get("version") or 1),
                        is_active=bool(t.get("is_active", True)),
                    )
                except Exception as ex_item:
                    app.logger.warning("template seed import failed for %s: %s", t.get("key"), ex_item)
    except Exception as ex:
        try:
            app.logger.warning("template seeding skipped: %s", ex)
        except Exception:
            pass


def _register_bp(app: Flask, bp: Blueprint, name: str) -> None:
    try:
        app.register_blueprint(bp)
    except Exception as ex:
        try:
            app.logger.exception("register %s failed: %s", name, ex)
        except Exception:
            pass


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    app.config.from_object(Config)

    # ── Defaults ──
    app.config.setdefault("VECTOPLAN_HOST", "https://vectoplan.com")
    app.config.setdefault("KEEP_VERSIONS_PER_PROJECT", 10)
    app.config.setdefault("SPECKLE_UPLOAD_TIMEOUT", 300)
    app.config.setdefault("MAX_CONTENT_LENGTH", 512 * 1024 * 1024)  # 512 MB
    # Wichtig: kein automatischer Upload ohne ChatAI-Freigabe
    app.config.setdefault("AUTO_UPLOAD_ATTACHMENTS", False)
    # Inline-B64 für kleine Anhänge (Bytes)
    app.config.setdefault("ATTACHMENT_INLINE_BASE64_MAX", 10 * 1024 * 1024)  # 10 MB
    # Obergrenze für Base64-Uploads (MB)
    app.config.setdefault("BASE64_UPLOAD_MAX_MB", 50)
    app.config.setdefault("FILE_CACHE_MAX_AGE", 3600)
    app.config.setdefault("FILE_CONTENT_CACHE_MAX_AGE", 3600)
    # Templates/State
    app.config.setdefault("ENABLE_TEMPLATE_API", True)
    app.config.setdefault("TEMPLATE_SEED", [])
    app.config.setdefault("TEMPLATE_SEED_PATH", None)
    app.config.setdefault("TEMPLATE_IMPORT_TO_DB_ON_STARTUP", False)

    # UI-Integrationen Defaults (werden von Config i.d.R. überschrieben)
    app.config.setdefault("CRAWLAB_PUBLIC_URL", "http://localhost:8080")
    app.config.setdefault("CRAWLAB_INTERNAL_URL", "http://crawlab:8080")
    app.config.setdefault("CRAWLAB_BASE_PATH", "/")
    app.config.setdefault("SUPERSET_PUBLIC_URL", "http://localhost:8088")
    app.config.setdefault("SUPERSET_INTERNAL_URL", "http://superset:8088")
    app.config.setdefault("SUPERSET_BASE_PATH", "/")

    # ── Logging + DB ──
    try:
        init_logging()
    except Exception:
        pass
    try:
        db.init_app(app)
    except Exception as ex:
        try:
            app.logger.exception("db.init_app failed: %s", ex)
        except Exception:
            pass

    # ── Medienverzeichnis ──
    try:
        media_root = app.config.get("MEDIA_ROOT")
        if media_root:
            Path(media_root).mkdir(parents=True, exist_ok=True)
    except Exception as ex:
        try:
            app.logger.warning("MEDIA_ROOT konnte nicht erstellt werden: %s", ex)
        except Exception:
            pass

    # ── Health ──
    sys_bp = Blueprint("system", __name__)

    @sys_bp.get("/health")
    def health():
        return jsonify({"status": "ok"})

    _register_bp(app, sys_bp, "system")

    # ── Routen ──
    _register_bp(app, ui_chat_bp, "ui_chat_bp")
    _register_bp(app, chat_api_bp, "chat_api_bp")
    _register_bp(app, files_bp, "files_bp")
    _register_bp(app, embed_bp, "embed_bp")
    _register_bp(app, speckle_upload_bp, "speckle_upload_bp")
    _register_bp(app, blobs_base64_bp, "blobs_base64_bp")   # NEU
    if ui_2dviewer_bp:
        _register_bp(app, ui_2dviewer_bp, "ui_2dviewer_bp")

    _register_bp(app, ui_map_bp, "ui_map_bp")
    _register_bp(app, vectoplan_ingest_bp, "vectoplan_ingest_bp")
    _register_bp(app, viewer_selection_bp, "viewer_selection_bp")
    _register_bp(app, versions_api_bp, "versions_api_bp")   # NEU
    _register_bp(app, vectoplan_align_bp, "vectoplan_align_bp")

    # ── UI Integrationen (Crawlab / Superset) ──
    try:
        ui_int = _try_import_ui_integrations()
        if ui_int.get("ui_crawlab_bp"):
            _register_bp(app, ui_int["ui_crawlab_bp"], "ui_crawlab_bp")  # type: ignore[arg-type]
        if ui_int.get("ui_superset_bp"):
            _register_bp(app, ui_int["ui_superset_bp"], "ui_superset_bp")  # type: ignore[arg-type]
    except Exception as ex:
        try:
            app.logger.exception("register ui integrations failed: %s", ex)
        except Exception:
            pass

    # Optionale Blueprints
    try:
        opt = _try_import_optional_blueprints()
        if app.config.get("ENABLE_TEMPLATE_API", True) and opt.get("templates_api_bp"):
            _register_bp(app, opt["templates_api_bp"], "templates_api_bp")  # type: ignore[arg-type]
        if opt.get("state_api_bp"):
            _register_bp(app, opt["state_api_bp"], "state_api_bp")  # type: ignore[arg-type]
    except Exception as ex:
        try:
            app.logger.exception("register optional blueprints failed: %s", ex)
        except Exception:
            pass

    # ── Seeds ──
    try:
        with app.app_context():
            _seed_templates_if_configured(app)
    except Exception as ex:
        try:
            app.logger.warning("template seed on startup failed: %s", ex)
        except Exception:
            pass

    # ── Security Headers ──
    @app.after_request
    def _secure_headers(resp):
        """
        Wichtig:
          - setdefault überschreibt keine explizit gesetzten Header
          - X-Frame-Options NICHT erzwingen, wenn die Route bewusst Embedding erlaubt
            (via allow_embed=1 oder CSP frame-ancestors ...)
        """
        try:
            resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        except Exception:
            pass

        # Referrer
        try:
            resp.headers.setdefault("Referrer-Policy", "no-referrer")
        except Exception:
            pass

        # Frame-Policy
        try:
            allow_embed = False
            try:
                allow_embed = (request.args.get("allow_embed") == "1")
            except Exception:
                allow_embed = False

            csp = ""
            try:
                csp = str(resp.headers.get("Content-Security-Policy", "") or "")
            except Exception:
                csp = ""

            csp_has_frame_ancestors = ("frame-ancestors" in csp.lower()) if csp else False

            # Nur setzen, wenn nicht explizit erlaubt/konfiguriert
            if not allow_embed and not csp_has_frame_ancestors:
                resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        except Exception:
            try:
                resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            except Exception:
                pass

        return resp

    # ── Fehlerbehandlung ──
    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"error": "payload too large"}), 413

    @app.errorhandler(404)
    def not_found(_e):
        try:
            if request.path.startswith("/v1/") or request.path.startswith("/ui/"):
                return jsonify({"error": "not found"}), 404
            return _e
        except Exception:
            return jsonify({"error": "not found"}), 404

    @app.errorhandler(Exception)
    def internal_error(e: Exception):
        try:
            current_app.logger.exception("Unhandled error")
        except Exception:
            pass
        try:
            if request.path.startswith("/v1/") or request.path.startswith("/ui/"):
                return jsonify({"error": str(e)}), 500
            return jsonify({"error": "internal error"}), 500
        except Exception:
            return jsonify({"error": "internal error"}), 500

    return app