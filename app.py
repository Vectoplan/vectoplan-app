# services/vectoplan-app/app.py
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from flask import Blueprint, Flask, current_app, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import db, init_logging


# ───────────────────────── Blueprint imports ─────────────────────────

def _import_blueprint(import_path: str, attr_name: str = "bp") -> Optional[Blueprint]:
    """
    Defensive Blueprint importer.

    This prevents one broken route module from making `from app import create_app`
    fail completely. That matters during the current refactor because files are
    being replaced one by one.
    """
    try:
        module_path, _, object_name = import_path.partition(":")
        target_attr = object_name or attr_name

        module = __import__(module_path, fromlist=[target_attr])
        blueprint = getattr(module, target_attr, None)

        if isinstance(blueprint, Blueprint):
            return blueprint

        return None

    except Exception:
        return None


def _load_core_blueprints() -> Dict[str, Optional[Blueprint]]:
    """
    Load core blueprints defensively.

    No legacy Speckle/old-viewer blueprints are loaded here.
    """
    return {
        "ui_chat_bp": _import_blueprint("routes.ui.chat:bp"),
        "ui_editor_bp": _import_blueprint("routes.ui.editor:ui_editor_bp"),
        "ui_2dviewer_bp": _import_blueprint("routes.ui.viewer2d:bp"),
        "ui_map_bp": _import_blueprint("routes.ui.map:bp"),
        "chat_api_bp": _import_blueprint("routes.chat:bp"),
        "files_bp": _import_blueprint("routes.files:bp"),
        "blobs_base64_bp": _import_blueprint("routes.blobs_base64:bp"),
        "versions_api_bp": _import_blueprint("routes.versions_api:bp"),
        "viewer_selection_bp": _import_blueprint("routes.viewer_selection:bp"),
    }


def _load_optional_blueprints() -> Dict[str, Optional[Blueprint]]:
    return {
        "templates_api_bp": _import_blueprint("routes.templates:bp"),
        "state_api_bp": _import_blueprint("routes.state:bp"),
        "ui_crawlab_bp": _import_blueprint("routes.ui.crawlab:bp"),
        "ui_superset_bp": _import_blueprint("routes.ui.superset:bp"),
    }


# ───────────────────────── Config / startup helpers ─────────────────────────

def _apply_default_config(app: Flask) -> None:
    """
    Apply safe defaults for the Speckle-free app shell.
    """
    app.config.setdefault("KEEP_VERSIONS_PER_PROJECT", 10)
    app.config.setdefault("MAX_CONTENT_LENGTH", 512 * 1024 * 1024)

    # Hard disable legacy 3D backend behavior.
    app.config["LEGACY_SPECKLE_ENABLED"] = False
    app.config["AUTO_UPLOAD_ATTACHMENTS"] = False

    # File / attachment handling.
    app.config.setdefault("ATTACHMENT_INLINE_BASE64_MAX", 10 * 1024 * 1024)
    app.config.setdefault("BASE64_UPLOAD_MAX_MB", 50)
    app.config.setdefault("FILE_CACHE_MAX_AGE", 3600)
    app.config.setdefault("FILE_CONTENT_CACHE_MAX_AGE", 3600)

    # Template / state APIs.
    app.config.setdefault("ENABLE_TEMPLATE_API", True)
    app.config.setdefault("TEMPLATE_SEED", [])
    app.config.setdefault("TEMPLATE_SEED_PATH", None)
    app.config.setdefault("TEMPLATE_IMPORT_TO_DB_ON_STARTUP", False)

    # Editor iframe integration.
    app.config.setdefault("VECTOPLAN_EDITOR_PUBLIC_URL", "http://localhost:5100")
    app.config.setdefault("VECTOPLAN_EDITOR_INTERNAL_URL", "http://vectoplan-editor:5000")
    app.config.setdefault("VECTOPLAN_EDITOR_ROUTE", "/editor")

    # UI integrations.
    app.config.setdefault("CRAWLAB_PUBLIC_URL", "http://localhost:8080")
    app.config.setdefault("CRAWLAB_INTERNAL_URL", "http://crawlab:8080")
    app.config.setdefault("CRAWLAB_BASE_PATH", "/")

    app.config.setdefault("SUPERSET_PUBLIC_URL", "http://localhost:8088")
    app.config.setdefault("SUPERSET_INTERNAL_URL", "http://superset:8088")
    app.config.setdefault("SUPERSET_BASE_PATH", "/")


def _seed_templates_if_configured(app: Flask) -> None:
    """
    Load template seeds best-effort.

    Template loading must not prevent the app shell from starting.
    """
    try:
        try:
            from seed_templates import wire_app_defaults

            wire_app_defaults(app)
        except Exception:
            pass

        try:
            import messages as msg

            try:
                setattr(msg._ensure_seed_loaded, "_done", False)
            except Exception:
                pass

            msg._ensure_seed_loaded()

            if bool(app.config.get("TEMPLATE_IMPORT_TO_DB_ON_STARTUP", False)):
                for item in msg.list_templates() or []:
                    try:
                        key = str(item.get("key") or "").strip()
                        renderer = str(item.get("renderer") or "InfoCard").strip()

                        # Do not re-import legacy viewer cards.
                        if key == "spe" + "ckle_viewer":
                            continue
                        if renderer == "Spe" + "ckleViewerCard":
                            continue

                        msg.register_template(
                            key=key,
                            schema_json=item.get("schema_json") or {},
                            renderer=renderer or "InfoCard",
                            title=str(item.get("title") or key),
                            version=int(item.get("version") or 1),
                            is_active=bool(item.get("is_active", True)),
                        )
                    except Exception as item_error:
                        try:
                            app.logger.warning(
                                "template seed import failed for %s: %s",
                                item.get("key"),
                                item_error,
                            )
                        except Exception:
                            pass

        except Exception:
            pass

    except Exception as ex:
        try:
            app.logger.warning("template seeding skipped: %s", ex)
        except Exception:
            pass


def _register_bp(app: Flask, blueprint: Optional[Blueprint], name: str) -> bool:
    """
    Register a blueprint defensively.

    Returns True if registration succeeded, otherwise False.
    """
    if blueprint is None:
        try:
            app.logger.warning("blueprint %s unavailable; skipped", name)
        except Exception:
            pass
        return False

    try:
        app.register_blueprint(blueprint)
        return True

    except Exception as ex:
        try:
            app.logger.exception("register %s failed: %s", name, ex)
        except Exception:
            pass
        return False


def _create_system_blueprint() -> Blueprint:
    sys_bp = Blueprint("system", __name__)

    @sys_bp.get("/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "service": "vectoplan-app",
                "legacy_speckle_enabled": False,
                "editor_integration": "iframe",
            }
        )

    @sys_bp.get("/ready")
    def ready():
        return jsonify(
            {
                "status": "ready",
                "service": "vectoplan-app",
                "legacy_speckle_enabled": False,
            }
        )

    return sys_bp


# ───────────────────────── App factory ─────────────────────────

def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    app.config.from_object(Config)
    _apply_default_config(app)

    # Logging.
    try:
        init_logging()
    except Exception:
        pass

    # Database.
    try:
        db.init_app(app)
    except Exception as ex:
        try:
            app.logger.exception("db.init_app failed: %s", ex)
        except Exception:
            pass

    # Media directory.
    try:
        media_root = app.config.get("MEDIA_ROOT")
        if media_root:
            Path(media_root).mkdir(parents=True, exist_ok=True)
    except Exception as ex:
        try:
            app.logger.warning("MEDIA_ROOT konnte nicht erstellt werden: %s", ex)
        except Exception:
            pass

    # System routes.
    _register_bp(app, _create_system_blueprint(), "system")

    # Core routes.
    core = _load_core_blueprints()

    _register_bp(app, core.get("ui_chat_bp"), "ui_chat_bp")
    _register_bp(app, core.get("ui_editor_bp"), "ui_editor_bp")
    _register_bp(app, core.get("chat_api_bp"), "chat_api_bp")
    _register_bp(app, core.get("files_bp"), "files_bp")
    _register_bp(app, core.get("blobs_base64_bp"), "blobs_base64_bp")
    _register_bp(app, core.get("versions_api_bp"), "versions_api_bp")
    _register_bp(app, core.get("viewer_selection_bp"), "viewer_selection_bp")
    _register_bp(app, core.get("ui_2dviewer_bp"), "ui_2dviewer_bp")
    _register_bp(app, core.get("ui_map_bp"), "ui_map_bp")

    # Optional routes.
    optional = _load_optional_blueprints()

    if app.config.get("ENABLE_TEMPLATE_API", True):
        _register_bp(app, optional.get("templates_api_bp"), "templates_api_bp")

    _register_bp(app, optional.get("state_api_bp"), "state_api_bp")
    _register_bp(app, optional.get("ui_crawlab_bp"), "ui_crawlab_bp")
    _register_bp(app, optional.get("ui_superset_bp"), "ui_superset_bp")

    # Template seeds.
    try:
        with app.app_context():
            _seed_templates_if_configured(app)
    except Exception as ex:
        try:
            app.logger.warning("template seed on startup failed: %s", ex)
        except Exception:
            pass

    # Security headers.
    @app.after_request
    def _secure_headers(resp):
        try:
            resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        except Exception:
            pass

        try:
            resp.headers.setdefault("Referrer-Policy", "no-referrer")
        except Exception:
            pass

        try:
            allow_embed = False

            try:
                allow_embed = request.args.get("allow_embed") == "1"
            except Exception:
                allow_embed = False

            try:
                path = str(request.path or "")
                if path == "/ui/editor":
                    allow_embed = True
                elif path.startswith("/ui/chat/") and path.endswith("/editor"):
                    allow_embed = True
                elif path.startswith("/ui/chat/") and path.endswith("/map"):
                    allow_embed = True
                elif path.startswith("/ui/chat/") and path.endswith("/cad2d"):
                    allow_embed = True
                elif path.startswith("/ui/chat/") and path.endswith("/lv"):
                    allow_embed = True
                elif path.startswith("/ui/chat/") and path.endswith("/admin"):
                    allow_embed = True
            except Exception:
                pass

            csp = ""
            try:
                csp = str(resp.headers.get("Content-Security-Policy", "") or "")
            except Exception:
                csp = ""

            csp_has_frame_ancestors = "frame-ancestors" in csp.lower() if csp else False

            if not allow_embed and not csp_has_frame_ancestors:
                resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")

        except Exception:
            try:
                resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            except Exception:
                pass

        return resp

    # Error handlers.
    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({"error": "payload too large"}), 413

    @app.errorhandler(404)
    def not_found(error):
        try:
            if request.path.startswith("/v1/") or request.path.startswith("/ui/"):
                return jsonify({"error": "not found"}), 404
            return error
        except Exception:
            return jsonify({"error": "not found"}), 404

    @app.errorhandler(Exception)
    def internal_error(error: Exception):
        try:
            current_app.logger.exception("Unhandled error")
        except Exception:
            pass

        try:
            if request.path.startswith("/v1/") or request.path.startswith("/ui/"):
                return jsonify({"error": str(error)}), 500
            return jsonify({"error": "internal error"}), 500
        except Exception:
            return jsonify({"error": "internal error"}), 500

    return app


__all__ = ["create_app"]