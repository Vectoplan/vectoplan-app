# services/vectoplan-app/app.py
from __future__ import annotations

from pathlib import Path
import importlib
import threading
from typing import Any, Dict, List, Mapping, Optional, Tuple

from flask import Blueprint, Flask, current_app, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import db, init_logging


# ─────────────────────────────────────────────────────────────
# Import/cache state
# ─────────────────────────────────────────────────────────────

_BLUEPRINT_IMPORT_LOCK = threading.RLock()
_BLUEPRINT_IMPORT_CACHE: Dict[str, Optional[Blueprint]] = {}
_BLUEPRINT_IMPORT_ERRORS: Dict[str, Dict[str, str]] = {}


# ─────────────────────────────────────────────────────────────
# Safe helpers
# ─────────────────────────────────────────────────────────────

def _safe_str(value: Any, default: str = "", max_len: int = 4000) -> str:
    try:
        text = str(value if value is not None else default).strip()

        if not text:
            text = default

        if max_len > 0 and len(text) > max_len:
            return text[:max_len]

        return text
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        if value is None:
            return default

        if isinstance(value, (int, float)):
            return bool(value)

        text = str(value).strip().lower()

        if text in {"1", "true", "yes", "y", "on", "ja", "enabled", "enable"}:
            return True

        if text in {"0", "false", "no", "n", "off", "nein", "disabled", "disable"}:
            return False

        return default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return dict(value.to_dict())
        return {}
    except Exception:
        return {}


def _error_payload(exc: BaseException) -> Dict[str, str]:
    try:
        return {
            "type": exc.__class__.__name__,
            "message": str(exc),
        }
    except Exception:
        return {
            "type": "Exception",
            "message": "unknown error",
        }


def _config_bool(app: Flask, key: str, default: bool = False) -> bool:
    try:
        return _safe_bool(app.config.get(key, default), default)
    except Exception:
        return default


def _config_str(app: Flask, key: str, default: str = "") -> str:
    try:
        value = app.config.get(key, default)
        text = str(value if value is not None else "").strip()
        return text or default
    except Exception:
        return default


def _config_int(app: Flask, key: str, default: int = 0) -> int:
    try:
        return _safe_int(app.config.get(key, default), default)
    except Exception:
        return default


def _status_ok(value: Any) -> bool:
    try:
        data = _safe_dict(value)
        return bool(data.get("ok", False))
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
# Blueprint imports
# ─────────────────────────────────────────────────────────────

def _import_blueprint(import_path: str, attr_name: str = "bp") -> Optional[Blueprint]:
    """
    Defensive Blueprint importer.

    Prevents one broken route module from making `from app import create_app`
    fail completely. The exact error is stored for /ready diagnostics.
    """
    cache_key = f"{import_path}|{attr_name}"

    with _BLUEPRINT_IMPORT_LOCK:
        if cache_key in _BLUEPRINT_IMPORT_CACHE:
            return _BLUEPRINT_IMPORT_CACHE.get(cache_key)

        try:
            module_path, _, object_name = import_path.partition(":")
            target_attr = object_name or attr_name

            module = importlib.import_module(module_path)
            blueprint = getattr(module, target_attr, None)

            if isinstance(blueprint, Blueprint):
                _BLUEPRINT_IMPORT_CACHE[cache_key] = blueprint
                _BLUEPRINT_IMPORT_ERRORS.pop(cache_key, None)
                return blueprint

            _BLUEPRINT_IMPORT_CACHE[cache_key] = None
            _BLUEPRINT_IMPORT_ERRORS[cache_key] = {
                "type": "BlueprintMissing",
                "message": f"{module_path}.{target_attr} is not a flask.Blueprint",
            }
            return None

        except Exception as exc:
            _BLUEPRINT_IMPORT_CACHE[cache_key] = None
            _BLUEPRINT_IMPORT_ERRORS[cache_key] = _error_payload(exc)
            return None


def _load_core_blueprints() -> Dict[str, Optional[Blueprint]]:
    """
    Load core blueprints defensively.

    No legacy Speckle/old-viewer blueprints are loaded here.
    """
    return {
        # Auth diagnostics.
        # This file is created in the next step. Until then it is skipped safely.
        "auth_status_api_bp": _import_blueprint("routes.auth_status_api:bp"),

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

def _apply_default_config(app: Flask) -> None:
    """
    Apply safe defaults for the auth-backed app shell and project layer.

    No local default user.
    No dev identity fallback.
    No placeholder invitation dispatch.
    """
    app.config.setdefault("KEEP_VERSIONS_PER_PROJECT", 10)
    app.config.setdefault("MAX_CONTENT_LENGTH", 512 * 1024 * 1024)

    # App database/bootstrap.
    app.config.setdefault("VECTOPLAN_APP_AUTO_CREATE_ALL", True)
    app.config.setdefault("VECTOPLAN_APP_ENSURE_DEFAULT_USER", False)
    app.config.setdefault("VECTOPLAN_DEFAULT_USER_ID", None)
    app.config.setdefault("VECTOPLAN_ALLOW_USER_HEADER_OVERRIDE", False)

    # Auth phase defaults.
    app.config.setdefault("VECTOPLAN_AUTH_MODE", "external")
    app.config.setdefault("VECTOPLAN_TRUST_AUTH_HEADERS", False)
    app.config.setdefault("VECTOPLAN_FORCE_DEMO_MODE", False)
    app.config.setdefault("VECTOPLAN_ALLOW_DEMO_QUERY_PARAM", False)
    app.config.setdefault("VECTOPLAN_DEMO_TTL_SECONDS", 3600)

    # vectoplan-auth URLs.
    # INTERNAL_URL is server-to-server and must be reachable from the app container.
    # PUBLIC_URL is browser-facing and only used for links/redirects.
    app.config.setdefault("VECTOPLAN_AUTH_INTERNAL_URL", "http://vectoplan-auth:5000")
    app.config.setdefault("VECTOPLAN_AUTH_PUBLIC_URL", "http://localhost:5000")
    app.config.setdefault("VECTOPLAN_AUTH_CONTEXT_MINIMAL_PATH", "/auth/context/minimal")
    app.config.setdefault("VECTOPLAN_AUTH_CONTEXT_PATH", "/auth/context")
    app.config.setdefault("VECTOPLAN_AUTH_ME_PATH", "/auth/me")
    app.config.setdefault("VECTOPLAN_AUTH_READY_PATH", "/health")
    app.config.setdefault("VECTOPLAN_AUTH_REQUEST_TIMEOUT_SECONDS", 2.0)
    app.config.setdefault("VECTOPLAN_AUTH_NEGATIVE_CACHE_TTL_SECONDS", 5)
    app.config.setdefault("VECTOPLAN_AUTH_STATUS_CACHE_TTL_SECONDS", 5)

    # Auth identity / invitation bridge.
    app.config.setdefault("AUTH_IDENTITY_INTERNAL_URL", app.config.get("VECTOPLAN_AUTH_INTERNAL_URL", ""))
    app.config.setdefault("AUTH_IDENTITY_LOOKUP_PATH", "/v1/auth/identity/lookup-email")
    app.config.setdefault("AUTH_IDENTITY_INVITATION_DISPATCH_PATH", "/v1/auth/invitations/project")
    app.config.setdefault("AUTH_IDENTITY_API_TOKEN", "")
    app.config.setdefault("AUTH_IDENTITY_DEV_MODE", False)
    app.config.setdefault("AUTH_IDENTITY_DEV_REGISTERED_EMAILS", "")
    app.config.setdefault("AUTH_IDENTITY_DEV_ACCEPT_ALL_REGISTERED", False)
    app.config.setdefault("AUTH_IDENTITY_PLACEHOLDER_INVITES", False)

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

    # Stateless 2D/CAD workspace integration.
    app.config.setdefault("VECTOPLAN_CAD_PUBLIC_URL", "http://localhost:5104")
    app.config.setdefault("VECTOPLAN_CAD_INTERNAL_URL", "http://vectoplan-cad:5000")
    app.config.setdefault("VECTOPLAN_CAD_ROUTE", "/cad")
    app.config.setdefault("VECTOPLAN_CAD_EMBED_ENABLED", True)

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
        "default_user_removed": True,
    }

    try:
        import models

        classes: Tuple[Any, ...] = ()

        try:
            if hasattr(models, "register_all_models"):
                classes = tuple(models.register_all_models() or ())
            elif hasattr(models, "get_core_model_classes"):
                classes = tuple(models.get_core_model_classes() or ())
        except Exception as class_error:
            status["class_error"] = f"{class_error.__class__.__name__}: {class_error}"

        try:
            if hasattr(models, "get_model_import_status"):
                model_status = _safe_dict(models.get_model_import_status())
                status.update(
                    {
                        "ok": bool(model_status.get("ok", False)),
                        "model_count": int(model_status.get("model_count") or len(classes or ())),
                        "tables": list(model_status.get("tables") or []),
                        "errors": dict(model_status.get("errors") or {}),
                        "missing_required_modules": list(model_status.get("missing_required_modules") or []),
                        "appChunkModelShapeReady": bool(model_status.get("appChunkModelShapeReady", False)),
                        "projectInvitationModelShapeReady": bool(model_status.get("projectInvitationModelShapeReady", False)),
                        "authLinkModelShapeReady": bool(model_status.get("authLinkModelShapeReady", False)),
                        "projectAuthContextShapeReady": bool(model_status.get("projectAuthContextShapeReady", False)),
                        "default_user_removed": bool(model_status.get("defaultUserRemoved", True)),
                    }
                )
            else:
                status.update(
                    {
                        "ok": True,
                        "model_count": len(tuple(classes or ())),
                        "tables": [],
                        "errors": {},
                    }
                )
        except Exception as status_error:
            status.update(
                {
                    "ok": bool(classes),
                    "model_count": len(tuple(classes or ())),
                    "tables": [],
                    "errors": {
                        "model_status": {
                            "type": status_error.__class__.__name__,
                            "message": str(status_error),
                        }
                    },
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
    - If an old local DB already has previous tables, a reset or migration is
      still required for new/changed columns.
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
            model_status = _import_all_models(app)
            app.extensions["vectoplan_model_status"] = model_status
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


def _default_user_removed_status(app: Flask) -> Dict[str, Any]:
    """
    Startup diagnostic for the removed default-user bootstrap.

    This intentionally does not import or call ensure_default_user().
    """
    return {
        "enabled": False,
        "ok": True,
        "skipped": True,
        "removed": True,
        "user_id": None,
        "reason": "vectoplan-auth_is_user_truth",
        "config_requested": _config_bool(app, "VECTOPLAN_APP_ENSURE_DEFAULT_USER", False),
    }


def _run_startup_database_tasks(app: Flask) -> None:
    """
    Run model import and optional create_all.

    Failure must not prevent the app from starting during refactor; routes will
    report JSON errors if the DB is not ready.
    """
    try:
        model_status = _import_all_models(app)
        app.extensions["vectoplan_model_status"] = model_status
    except Exception as ex:
        app.extensions["vectoplan_model_status"] = {
            "ok": False,
            "error": f"{ex.__class__.__name__}: {ex}",
            "default_user_removed": True,
        }

    try:
        schema_status = _create_database_schema_if_configured(app)
        app.extensions["vectoplan_schema_status"] = schema_status
    except Exception as ex:
        app.extensions["vectoplan_schema_status"] = {
            "ok": False,
            "error": f"{ex.__class__.__name__}: {ex}",
        }

    try:
        removed_status = _default_user_removed_status(app)
        app.extensions["vectoplan_default_user_status"] = removed_status
    except Exception as ex:
        app.extensions["vectoplan_default_user_status"] = {
            "ok": False,
            "removed": True,
            "error": f"{ex.__class__.__name__}: {ex}",
        }


def _auth_config_status(app: Flask) -> Dict[str, Any]:
    internal_url = _config_str(app, "VECTOPLAN_AUTH_INTERNAL_URL", "")
    public_url = _config_str(app, "VECTOPLAN_AUTH_PUBLIC_URL", "")

    return {
        "configured": bool(internal_url),
        "internal_url_configured": bool(internal_url),
        "public_url_configured": bool(public_url),
        "internal_url": internal_url,
        "public_url": public_url,
        "mode": _config_str(app, "VECTOPLAN_AUTH_MODE", "external"),
        "timeout_seconds": _safe_float(app.config.get("VECTOPLAN_AUTH_REQUEST_TIMEOUT_SECONDS"), 2.0),
        "fail_closed": True,
    }


def _blueprint_status(app: Flask) -> Dict[str, Any]:
    registered: List[str] = []
    try:
        registered = sorted(str(name) for name in app.blueprints.keys())
    except Exception:
        registered = []

    return {
        "registered": registered,
        "import_errors": dict(_BLUEPRINT_IMPORT_ERRORS),
    }


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
                "auth_truth": "vectoplan-auth",
                "default_user_removed": True,
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
            "auth_truth": "vectoplan-auth",
            "default_user_removed": True,
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
            payload["default_user"] = {
                "removed": True,
                "ok": True,
            }

        try:
            payload["auth"] = _auth_config_status(current_app)
        except Exception:
            payload["auth"] = {}

        try:
            payload["blueprints"] = _blueprint_status(current_app)
        except Exception:
            payload["blueprints"] = {}

        try:
            model_status = _safe_dict(payload.get("models"))
            schema_status = _safe_dict(payload.get("schema"))
            payload["ready"] = bool(model_status.get("ok", False) and schema_status.get("ok", True))
        except Exception:
            payload["ready"] = False

        status_code = 200 if payload.get("ready") else 503
        return jsonify(payload), status_code

    return sys_bp


# ─────────────────────────────────────────────────────────────
# Security headers
# ─────────────────────────────────────────────────────────────

def _allowed_frame_ancestors(app: Flask, *, desktop_embed: bool = False) -> str:
    allowed = (
        "'self' "
        "http://localhost:5103 "
        "http://127.0.0.1:5103 "
        "http://localhost:5200 "
        "http://127.0.0.1:5200"
    )
    if desktop_embed:
        # The native launcher intentionally serves its trusted shell from a
        # packaged file:// URL. Only the explicit allow_embed=1 request may
        # therefore be framed by a local desktop document.
        allowed = f"{allowed} file:"

    try:
        extra = (
            app.config.get("APP_FRAME_ANCESTORS")
            or app.config.get("VECTOPLAN_ALLOWED_FRAME_PARENTS")
            or app.config.get("VECTOPLAN_EDITOR_FRAME_ANCESTORS")
            or ""
        )
        extra = str(extra or "").strip()
        if extra:
            allowed = f"{allowed} {extra}"
    except Exception:
        pass

    deduped: List[str] = []
    try:
        for item in allowed.split():
            item = item.strip()
            if item and item not in deduped:
                deduped.append(item)
    except Exception:
        pass

    return " ".join(deduped or ["'self'"])


def _request_allows_desktop_embed() -> bool:
    try:
        return request.args.get("allow_embed") == "1"
    except Exception:
        return False


def _request_allows_embed() -> bool:
    allow_embed = _request_allows_desktop_embed()

    try:
        path = str(request.path or "")

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

    return bool(allow_embed)


def _merge_csp_frame_ancestors(existing_csp: str, frame_ancestors_directive: str) -> str:
    csp = _safe_str(existing_csp, "", 10000)
    directive_value = _safe_str(frame_ancestors_directive, "frame-ancestors 'self'", 2000)

    if not csp:
        return directive_value

    directives: List[str] = []
    replaced = False

    try:
        for directive in csp.split(";"):
            directive = directive.strip()
            if not directive:
                continue

            if directive.lower().startswith("frame-ancestors"):
                directives.append(directive_value)
                replaced = True
            else:
                directives.append(directive)

        if not replaced:
            directives.append(directive_value)

        return "; ".join(directives)
    except Exception:
        return directive_value


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

    # Import models / create missing tables.
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

    # Auth diagnostic route first, once the file exists.
    _register_bp(app, core.get("auth_status_api_bp"), "auth_status_api_bp")

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
            allow_embed = _request_allows_embed()
            desktop_embed = _request_allows_desktop_embed()
            frame_ancestors = "frame-ancestors " + _allowed_frame_ancestors(
                app,
                desktop_embed=desktop_embed,
            )
            existing_csp = str(resp.headers.get("Content-Security-Policy", "") or "")
            resp.headers["Content-Security-Policy"] = _merge_csp_frame_ancestors(existing_csp, frame_ancestors)

            if allow_embed:
                try:
                    resp.headers.pop("X-Frame-Options", None)
                except Exception:
                    pass
            else:
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
                return jsonify(
                    {
                        "ok": False,
                        "error": str(error),
                        "code": "internal_error",
                    }
                ), 500

            return jsonify({"ok": False, "error": "internal error", "code": "internal_error"}), 500
        except Exception:
            return jsonify({"ok": False, "error": "internal error", "code": "internal_error"}), 500

    from vectoplan_i18n import init_app as init_i18n
    init_i18n(app)
    return app


__all__ = ["create_app"]
