# services/vectoplan-app/app.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, Flask, current_app, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import db, init_logging


# ─────────────────────────────────────────────────────────────
# Blueprint imports
# ─────────────────────────────────────────────────────────────

def _import_blueprint(import_path: str, attr_name: str = "bp") -> Optional[Blueprint]:
    """
    Defensive Blueprint importer.

    Prevents one broken route module from making `from app import create_app`
    fail completely. This matters during the current refactor because files are
    replaced one by one.
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
        # Project/API layer.
        "projects_api_bp": _import_blueprint("routes.projects_api:bp"),

        # App-shell project routes.
        # Must be registered before routes.viewer because /project=<id>
        # belongs to the shell, not directly to the iframe workspace.
        "ui_projects_bp": _import_blueprint("routes.ui.projects:bp"),

        # New project/workspace viewer routes.
        # Owns /ui/project/... iframe destinations and context.json.
        "viewer_bp": _import_blueprint("routes.viewer:bp"),

        # Existing shell/workspace/chat routes.
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


# ─────────────────────────────────────────────────────────────
# Config / startup helpers
# ─────────────────────────────────────────────────────────────

def _config_bool(app: Flask, key: str, default: bool = False) -> bool:
    try:
        value = app.config.get(key, default)

        if isinstance(value, bool):
            return value

        text = str(value if value is not None else "").strip().lower()

        if text in {"1", "true", "yes", "y", "on", "ja"}:
            return True

        if text in {"0", "false", "no", "n", "off", "nein"}:
            return False

        return default

    except Exception:
        return default


def _config_str(app: Flask, key: str, default: str = "") -> str:
    try:
        value = app.config.get(key, default)
        text = str(value if value is not None else "").strip()
        return text or default
    except Exception:
        return default


def _apply_default_config(app: Flask) -> None:
    """
    Apply safe defaults for the Speckle-free app shell and new project layer.
    """
    app.config.setdefault("KEEP_VERSIONS_PER_PROJECT", 10)
    app.config.setdefault("MAX_CONTENT_LENGTH", 512 * 1024 * 1024)

    # Project-management phase defaults.
    app.config.setdefault("VECTOPLAN_DEFAULT_USER_ID", 1)
    app.config.setdefault("VECTOPLAN_APP_AUTO_CREATE_ALL", True)
    app.config.setdefault("VECTOPLAN_APP_ENSURE_DEFAULT_USER", True)
    app.config.setdefault("VECTOPLAN_ALLOW_USER_HEADER_OVERRIDE", False)

    # Auth/demo phase defaults.
    # Current local development stays compatible with placeholder user id=1.
    app.config.setdefault("VECTOPLAN_AUTH_MODE", "dev")
    app.config.setdefault("VECTOPLAN_TRUST_AUTH_HEADERS", False)
    app.config.setdefault("VECTOPLAN_FORCE_DEMO_MODE", False)
    app.config.setdefault("VECTOPLAN_ALLOW_DEMO_QUERY_PARAM", True)
    app.config.setdefault("VECTOPLAN_DEMO_TTL_SECONDS", 1800)

    # Future Auth/Registration service bridge.
    app.config.setdefault("AUTH_IDENTITY_INTERNAL_URL", "")
    app.config.setdefault("AUTH_IDENTITY_LOOKUP_PATH", "/internal/auth/identity/lookup-email")
    app.config.setdefault("AUTH_IDENTITY_INVITATION_DISPATCH_PATH", "/internal/auth/invitations/project")
    app.config.setdefault("AUTH_IDENTITY_API_TOKEN", "")
    app.config.setdefault("AUTH_IDENTITY_DEV_MODE", True)
    app.config.setdefault("AUTH_IDENTITY_DEV_REGISTERED_EMAILS", "")
    app.config.setdefault("AUTH_IDENTITY_DEV_ACCEPT_ALL_REGISTERED", False)
    app.config.setdefault("AUTH_IDENTITY_PLACEHOLDER_INVITES", True)

    # Project publication / simplified form.
    app.config.setdefault("PROJECT_PUBLICATION_CACHE_TTL_SECONDS", 10)
    app.config.setdefault("VECTOPLAN_PROJECT_PAYLOAD_ALLOW_SYSTEM_REFS", False)

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

    # Chunk integration.
    app.config.setdefault("VECTOPLAN_CHUNK_INTERNAL_URL", "http://vectoplan-chunk:5000")
    app.config.setdefault("VECTOPLAN_CHUNK_PUBLIC_URL", "")
    app.config.setdefault("VECTOPLAN_CHUNK_PROVISION_ON_PROJECT_CREATE", True)
    app.config.setdefault("VECTOPLAN_CHUNK_PROVISION_REQUIRED", False)
    app.config.setdefault("VECTOPLAN_CHUNK_STATUS_CHECK_IN_APP_STATUS", False)

    # Editor iframe integration.
    app.config.setdefault("VECTOPLAN_EDITOR_PUBLIC_URL", "http://localhost:5100")
    app.config.setdefault("VECTOPLAN_EDITOR_INTERNAL_URL", "http://vectoplan-editor:5000")
    app.config.setdefault("VECTOPLAN_EDITOR_ROUTE", "/editor")
    app.config.setdefault("VECTOPLAN_EDITOR_EMBED_ENABLED", True)

    # OpenLayer / Map iframe integration.
    app.config.setdefault("OPENLAYER_PUBLIC_URL", "http://localhost:5190")
    app.config.setdefault("OPENLAYER_INTERNAL_URL", "http://openlayer:8090")
    app.config.setdefault("OPENLAYER_ROUTE", "/map")
    app.config.setdefault("OPENLAYER_EMBED_ENABLED", True)

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
        if blueprint.name in app.blueprints:
            try:
                app.logger.info("blueprint %s already registered; skipped duplicate", name)
            except Exception:
                pass
            return True

        app.register_blueprint(blueprint)
        return True

    except Exception as ex:
        try:
            app.logger.exception("register %s failed: %s", name, ex)
        except Exception:
            pass
        return False


def _import_all_models(app: Flask) -> Dict[str, Any]:
    """
    Import all SQLAlchemy models.

    This is required before db.create_all() and useful for diagnostics.
    """
    status: Dict[str, Any] = {
        "ok": False,
        "model_count": 0,
        "tables": [],
        "error": None,
    }

    try:
        import models

        try:
            if hasattr(models, "register_all_models"):
                classes = models.register_all_models()
            elif hasattr(models, "get_core_model_classes"):
                classes = models.get_core_model_classes()
            else:
                classes = ()
        except Exception:
            classes = ()

        try:
            if hasattr(models, "get_model_import_status"):
                model_status = models.get_model_import_status()
                status.update(
                    {
                        "ok": bool(model_status.get("core_loaded", True)),
                        "model_count": int(model_status.get("model_count") or 0),
                        "tables": list(model_status.get("tables") or []),
                        "optional_errors": dict(model_status.get("optional_errors") or {}),
                    }
                )
            else:
                status.update(
                    {
                        "ok": True,
                        "model_count": len(tuple(classes or ())),
                        "tables": [],
                        "optional_errors": {},
                    }
                )
        except Exception:
            status.update(
                {
                    "ok": True,
                    "model_count": len(tuple(classes or ())),
                    "tables": [],
                    "optional_errors": {},
                }
            )

        return status

    except Exception as ex:
        try:
            app.logger.exception("model import failed: %s", ex)
        except Exception:
            pass

        status["error"] = f"{ex.__class__.__name__}: {ex}"
        return status


def _create_database_schema_if_configured(app: Flask) -> Dict[str, Any]:
    """
    Best-effort schema creation for local alpha development.

    Important:
    - db.create_all() creates missing tables only.
    - It does not add missing columns to existing tables.
    - If an old local DB already has the previous `projects` table, a reset or
      migration is still required for the new Project columns.
    """
    result: Dict[str, Any] = {
        "enabled": _config_bool(app, "VECTOPLAN_APP_AUTO_CREATE_ALL", True),
        "ok": False,
        "created": False,
        "error": None,
    }

    if not result["enabled"]:
        result["ok"] = True
        return result

    try:
        with app.app_context():
            _import_all_models(app)
            db.create_all()

        result["ok"] = True
        result["created"] = True
        return result

    except Exception as ex:
        try:
            app.logger.exception("db.create_all failed: %s", ex)
        except Exception:
            pass

        result["error"] = f"{ex.__class__.__name__}: {ex}"
        return result


def _ensure_default_user_if_configured(app: Flask) -> Dict[str, Any]:
    """
    Ensure placeholder AppUser(id=1) exists.

    This is best-effort during startup. Project APIs also ensure it per request.

    In forced demo mode the placeholder user is intentionally not created.
    """
    result: Dict[str, Any] = {
        "enabled": _config_bool(app, "VECTOPLAN_APP_ENSURE_DEFAULT_USER", True),
        "ok": False,
        "user_id": None,
        "skipped": False,
        "error": None,
    }

    if not result["enabled"]:
        result["ok"] = True
        return result

    try:
        auth_mode = _config_str(app, "VECTOPLAN_AUTH_MODE", "dev").lower()
        force_demo = _config_bool(app, "VECTOPLAN_FORCE_DEMO_MODE", False)

        if force_demo or auth_mode == "demo":
            result["ok"] = True
            result["skipped"] = True
            result["reason"] = "demo_mode"
            return result

    except Exception:
        pass

    try:
        with app.app_context():
            from services.current_user import ensure_default_user

            user = ensure_default_user()
            result["user_id"] = getattr(user, "id", None)
            result["ok"] = user is not None

        return result

    except Exception as ex:
        try:
            app.logger.warning("ensure_default_user failed: %s", ex)
        except Exception:
            pass

        result["error"] = f"{ex.__class__.__name__}: {ex}"
        return result


def _run_startup_database_tasks(app: Flask) -> None:
    """
    Run model import, optional create_all and default-user bootstrap.

    Failure must not prevent the app from starting during refactor; routes will
    report JSON errors if the DB is not ready.
    """
    try:
        model_status = _import_all_models(app)
        app.extensions["vectoplan_model_status"] = model_status
    except Exception:
        pass

    try:
        schema_status = _create_database_schema_if_configured(app)
        app.extensions["vectoplan_schema_status"] = schema_status
    except Exception:
        pass

    try:
        user_status = _ensure_default_user_if_configured(app)
        app.extensions["vectoplan_default_user_status"] = user_status
    except Exception:
        pass


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
                "project_management": True,
                "project_viewer": True,
                "demo_mode_supported": True,
            }
        )

    @sys_bp.get("/ready")
    def ready():
        payload: Dict[str, Any] = {
            "status": "ready",
            "service": "vectoplan-app",
            "legacy_speckle_enabled": False,
            "project_management": True,
            "project_viewer": True,
            "demo_mode_supported": True,
        }

        try:
            payload["models"] = dict(current_app.extensions.get("vectoplan_model_status") or {})
        except Exception:
            payload["models"] = {}

        try:
            payload["schema"] = dict(current_app.extensions.get("vectoplan_schema_status") or {})
        except Exception:
            payload["schema"] = {}

        try:
            payload["default_user"] = dict(current_app.extensions.get("vectoplan_default_user_status") or {})
        except Exception:
            payload["default_user"] = {}

        try:
            payload["blueprints"] = sorted(str(name) for name in current_app.blueprints.keys())
        except Exception:
            payload["blueprints"] = []

        return jsonify(payload)

    return sys_bp


# ─────────────────────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────────────────────

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

    # Import models / create missing tables / ensure placeholder user.
    try:
        _run_startup_database_tasks(app)
    except Exception as ex:
        try:
            app.logger.warning("startup database tasks failed: %s", ex)
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

    # Project routes.
    # Order matters:
    # 1. API routes first.
    # 2. Shell routes next, so /project=<id> renders chat_viewer.html.
    # 3. Viewer routes after shell routes, so /ui/project/... renders iframe workspaces.
    _register_bp(app, core.get("projects_api_bp"), "projects_api_bp")
    _register_bp(app, core.get("ui_projects_bp"), "ui_projects_bp")
    _register_bp(app, core.get("viewer_bp"), "viewer_bp")

    # Existing app shell / chat / workspace routes.
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

                # Project workspaces are iframe destinations inside the app shell.
                if path == "/ui/editor":
                    allow_embed = True
                elif path == "/ui/project/new":
                    allow_embed = True
                elif path.startswith("/ui/project/"):
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
            elif allow_embed:
                try:
                    resp.headers.pop("X-Frame-Options", None)
                except Exception:
                    pass

        except Exception:
            try:
                resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            except Exception:
                pass

        return resp

    # Error handlers.
    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({"ok": False, "error": "payload too large", "code": "payload_too_large"}), 413

    @app.errorhandler(404)
    def not_found(error):
        try:
            path = str(request.path or "")

            if (
                path.startswith("/v1/")
                or path.startswith("/ui/")
                or path.startswith("/project")
            ):
                return jsonify({"ok": False, "error": "not found", "code": "not_found"}), 404

            return error
        except Exception:
            return jsonify({"ok": False, "error": "not found", "code": "not_found"}), 404

    @app.errorhandler(Exception)
    def internal_error(error: Exception):
        try:
            current_app.logger.exception("Unhandled error")
        except Exception:
            pass

        try:
            path = str(request.path or "")

            if (
                path.startswith("/v1/")
                or path.startswith("/ui/")
                or path.startswith("/project")
            ):
                return jsonify({"ok": False, "error": str(error), "code": "internal_error"}), 500

            return jsonify({"ok": False, "error": "internal error", "code": "internal_error"}), 500
        except Exception:
            return jsonify({"ok": False, "error": "internal error", "code": "internal_error"}), 500

    return app


__all__ = ["create_app"]