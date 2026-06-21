# services/vectoplan-app/routes/viewer_selection.py
from __future__ import annotations

from collections.abc import Mapping as MappingABC
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional

from flask import Blueprint, current_app, jsonify, make_response, request
from werkzeug.wrappers import Response

from extensions import db
from models import Conversation, ConversationState


bp = Blueprint("viewer_selection", __name__)


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

DEFAULT_MODE = "editor"
DEFAULT_WORKSPACE_MODE = "3d"

_ALLOWED_NORMAL_MODES = {
    "editor",
    "map",
    "2d",
    "lv",
    "admin",
}

_ALLOWED_WORKSPACE_MODES = {
    "3d",
    "map",
    "2d",
    "lv",
    "admin",
}

_MODE_ALIASES = {
    "": DEFAULT_MODE,
    "3d": DEFAULT_MODE,
    "editor": DEFAULT_MODE,
    "viewer": DEFAULT_MODE,
    "viewer3d": DEFAULT_MODE,
    "oldviewer": DEFAULT_MODE,
    "model": DEFAULT_MODE,
    "version": DEFAULT_MODE,
    "map": "map",
    "openlayer": "map",
    "karte": "map",
    "2d": "2d",
    "cad": "2d",
    "cad2d": "2d",
    "plan": "2d",
    "plan2d": "2d",
    "lv": "lv",
    "boq": "lv",
    "admin": "admin",
    "settings": "admin",
}

_WORKSPACE_ALIASES = {
    "": DEFAULT_WORKSPACE_MODE,
    "3d": "3d",
    "editor": "3d",
    "viewer": "3d",
    "viewer3d": "3d",
    "model": "3d",
    "version": "3d",
    "map": "map",
    "openlayer": "map",
    "karte": "map",
    "2d": "2d",
    "cad": "2d",
    "cad2d": "2d",
    "plan": "2d",
    "plan2d": "2d",
    "lv": "lv",
    "boq": "lv",
    "admin": "admin",
    "settings": "admin",
}

_WORKSPACE_BY_MODE = {
    "editor": "3d",
    "map": "map",
    "2d": "2d",
    "lv": "lv",
    "admin": "admin",
}

_MODE_BY_WORKSPACE = {
    "3d": "editor",
    "map": "map",
    "2d": "2d",
    "lv": "lv",
    "admin": "admin",
}

# Backend-/Altviewer-Felder werden bewusst nicht gespeichert.
# Diese Route heißt aus Kompatibilität weiter "viewer/selection",
# speichert aber nur neutralen Workspace-/UI-State.
_LEGACY_DROP_KEYS = {
    "project_id",
    "projectid",
    "model_id",
    "modelid",
    "version_id",
    "versionid",
    "commit_id",
    "commitid",
    "stream_id",
    "streamid",
    "branch_id",
    "branchid",
    "viewer_url",
    "viewerurl",
    "raw_viewer_url",
    "rawviewerurl",
    "external_viewer_url",
    "externalviewerurl",
    "embed_url",
    "embedurl",
    "iframe_url",
    "iframeurl",
    "vectoplan_project_id",
    "vectoplan_model_id",
    "vectoplan_version_id",
    "world_id",
    "worldid",
    "chunk_project_id",
    "chunk_world_id",
    "chunk_snapshot_id",
    "snapshot_id",
    "runtime_ref",
    "runtime_url",
}

_OPTIONAL_NEUTRAL_KEYS = {
    "updated_at",
    "last_2d_selection",
    "last_2d_selection_ts",
    "last_2d_hover",
    "last_2d_hover_ts",
    "last_editor_selection",
    "last_editor_selection_ts",
    "last_editor_message",
    "last_editor_message_ts",
    "last_map_selection",
    "last_map_selection_ts",
    "last_map_hover",
    "last_map_hover_ts",
    "last_workspace_error",
    "last_workspace_error_ts",
}

_MAX_JSON_DEPTH = 8
_MAX_LIST_ITEMS = 500
_MAX_STRING_LENGTH = 20_000


# ─────────────────────────────────────────────────────────────
# Logging helpers
# ─────────────────────────────────────────────────────────────

def _log_warning(message: str, *args: Any, **kwargs: Any) -> None:
    try:
        current_app.logger.warning(message, *args, **kwargs)
    except Exception:
        pass


def _log_exception(message: str, exc: Exception | None = None) -> None:
    try:
        if exc is None:
            current_app.logger.exception(message)
        else:
            current_app.logger.exception("%s: %s", message, exc.__class__.__name__)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Response helpers
# ─────────────────────────────────────────────────────────────

def _apply_no_cache_headers(response: Response) -> Response:
    try:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
    except Exception:
        pass

    return response


def _json_response(payload: Mapping[str, Any], status: int = 200) -> Response:
    response = jsonify(payload)
    response.status_code = status
    return _apply_no_cache_headers(response)


def _json_error(message: str, status: int = 400, *, code: Optional[str] = None) -> Response:
    payload: Dict[str, Any] = {
        "ok": False,
        "error": {
            "message": str(message or "error"),
        },
        "status": status,
        "legacy_3d_backend": False,
        "legacy_speckle": False,
    }

    if code:
        payload["error"]["code"] = code

    return _json_response(payload, status=status)


def _empty_response(status: int = 204) -> Response:
    response = make_response("", status)
    return _apply_no_cache_headers(response)


# ─────────────────────────────────────────────────────────────
# Generic helpers
# ─────────────────────────────────────────────────────────────

def _get_conversation(chat_id: str) -> Optional[Conversation]:
    try:
        return Conversation.query.get(str(chat_id))
    except Exception as exc:
        _log_exception("conversation lookup failed", exc)
        return None


def _safe_state_dict(row: Any) -> Dict[str, Any]:
    try:
        value = getattr(row, "state_json", None)

        if isinstance(value, MappingABC):
            return dict(value)

        return {}

    except Exception:
        return {}


def _get_state(chat_id: str) -> Dict[str, Any]:
    try:
        row = ConversationState.get_or_create(str(chat_id))
        return _safe_state_dict(row)
    except Exception:
        _log_warning("ConversationState.get_or_create failed", exc_info=True)
        return {}


def _request_json() -> Dict[str, Any]:
    try:
        payload = request.get_json(silent=True) or {}

        if isinstance(payload, MappingABC):
            return dict(payload)

        return {}

    except Exception:
        return {}


def _clean_text(value: Any, default: str = "", max_len: int = 240) -> str:
    try:
        text = str(value if value is not None else default).strip()

        if not text:
            text = default

        if max_len > 0 and len(text) > max_len:
            return text[:max_len]

        return text

    except Exception:
        return default


def _lower_text(value: Any, default: str = "", max_len: int = 120) -> str:
    try:
        return _clean_text(value, default=default, max_len=max_len).lower()
    except Exception:
        return default


@lru_cache(maxsize=128)
def _normalize_mode_cached(raw: str) -> str:
    try:
        value = _lower_text(raw, DEFAULT_MODE, 120)
        mode = _MODE_ALIASES.get(value, value)

        if mode not in _ALLOWED_NORMAL_MODES:
            return DEFAULT_MODE

        return mode

    except Exception:
        return DEFAULT_MODE


def _normalize_mode(value: Any) -> str:
    try:
        return _normalize_mode_cached(_clean_text(value, DEFAULT_MODE, 120))
    except Exception:
        return DEFAULT_MODE


@lru_cache(maxsize=128)
def _normalize_workspace_cached(raw: str) -> str:
    try:
        value = _lower_text(raw, DEFAULT_WORKSPACE_MODE, 120)
        workspace = _WORKSPACE_ALIASES.get(value, value)

        if workspace not in _ALLOWED_WORKSPACE_MODES:
            return DEFAULT_WORKSPACE_MODE

        return workspace

    except Exception:
        return DEFAULT_WORKSPACE_MODE


def _normalize_workspace_mode(value: Any) -> str:
    try:
        return _normalize_workspace_cached(_clean_text(value, DEFAULT_WORKSPACE_MODE, 120))
    except Exception:
        return DEFAULT_WORKSPACE_MODE


def _workspace_mode_for(mode: Any) -> str:
    try:
        normalized = _normalize_mode(mode)
        return _WORKSPACE_BY_MODE.get(normalized, DEFAULT_WORKSPACE_MODE)
    except Exception:
        return DEFAULT_WORKSPACE_MODE


def _mode_for_workspace(workspace_mode: Any) -> str:
    try:
        workspace = _normalize_workspace_mode(workspace_mode)
        return _MODE_BY_WORKSPACE.get(workspace, DEFAULT_MODE)
    except Exception:
        return DEFAULT_MODE


def _is_legacy_key(key: Any) -> bool:
    try:
        text = _lower_text(key, "", 160)

        if not text:
            return False

        if text in _LEGACY_DROP_KEYS:
            return True

        if text.startswith("spe" + "ckle"):
            return True

        if text.startswith("legacy_spe" + "ckle"):
            return True

        if text.startswith("legacy_viewer"):
            return True

        return False

    except Exception:
        return False


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_JSON_DEPTH:
        return None

    try:
        if isinstance(value, MappingABC):
            clean: Dict[str, Any] = {}

            for key, item in value.items():
                if _is_legacy_key(key):
                    continue

                key_text = _clean_text(key, "", 160)
                if not key_text:
                    continue

                clean[key_text] = _json_safe(item, depth=depth + 1)

            return clean

        if isinstance(value, list):
            result = []
            for item in value[:_MAX_LIST_ITEMS]:
                result.append(_json_safe(item, depth=depth + 1))
            return result

        if isinstance(value, tuple):
            result = []
            for item in list(value)[:_MAX_LIST_ITEMS]:
                result.append(_json_safe(item, depth=depth + 1))
            return result

        if isinstance(value, str):
            if len(value) > _MAX_STRING_LENGTH:
                return value[:_MAX_STRING_LENGTH]
            return value

        if isinstance(value, (int, float, bool)) or value is None:
            return value

        text = str(value)
        if len(text) > _MAX_STRING_LENGTH:
            return text[:_MAX_STRING_LENGTH]
        return text

    except Exception:
        return None


def _extract_nested_mapping(data: Mapping[str, Any], *keys: str) -> Dict[str, Any]:
    try:
        for key in keys:
            candidate = data.get(key)
            if isinstance(candidate, MappingABC):
                return dict(candidate)

        return {}

    except Exception:
        return {}


def _default_selection() -> Dict[str, Any]:
    return {
        "ok": True,
        "mode": DEFAULT_MODE,
        "workspace_mode": DEFAULT_WORKSPACE_MODE,
        "legacy_3d_backend": False,
        "legacy_speckle": False,
    }


def _selection_payload(data: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        nested = _extract_nested_mapping(data, "selection", "viewer_selection", "workspace_selection")
        source: Dict[str, Any] = dict(nested or data or {})

        raw_mode = (
            source.get("mode")
            if source.get("mode") is not None
            else source.get("viewer_mode")
        )

        raw_workspace = source.get("workspace_mode")

        if raw_mode is not None and _clean_text(raw_mode, ""):
            mode = _normalize_mode(raw_mode)
            workspace_mode = _workspace_mode_for(mode)
        elif raw_workspace is not None and _clean_text(raw_workspace, ""):
            workspace_mode = _normalize_workspace_mode(raw_workspace)
            mode = _mode_for_workspace(workspace_mode)
        else:
            mode = DEFAULT_MODE
            workspace_mode = DEFAULT_WORKSPACE_MODE

        selection: Dict[str, Any] = {
            "ok": True,
            "mode": mode,
            "workspace_mode": workspace_mode,
            "legacy_3d_backend": False,
            "legacy_speckle": False,
        }

        for key in _OPTIONAL_NEUTRAL_KEYS:
            if key in source:
                selection[key] = _json_safe(source.get(key))

        return selection

    except Exception:
        return _default_selection()


def _extract_selection_from_state(state: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        if isinstance(state.get("viewer_selection"), MappingABC):
            return _selection_payload(dict(state.get("viewer_selection") or {}))

        if isinstance(state.get("workspace_selection"), MappingABC):
            return _selection_payload(dict(state.get("workspace_selection") or {}))

        return _selection_payload(state)

    except Exception:
        return _default_selection()


def _copy_optional_neutral_state(
    *,
    source: Mapping[str, Any],
    target: Dict[str, Any],
) -> None:
    try:
        for key in _OPTIONAL_NEUTRAL_KEYS:
            if key in source:
                target[key] = _json_safe(source.get(key))
    except Exception:
        pass


def _merge_optional_state_into_selection(
    *,
    state: Mapping[str, Any],
    selection: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        merged = dict(selection)

        for key in _OPTIONAL_NEUTRAL_KEYS:
            if key in state and key not in merged:
                merged[key] = _json_safe(state.get(key))

        return merged

    except Exception:
        return selection


def _state_patch_from_payload(data: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Build a neutral ConversationState patch.

    This route is still named viewer/selection for compatibility, but it stores
    only neutral workspace state.
    """
    try:
        payload: Dict[str, Any] = dict(data or {})
        nested_selection = _extract_nested_mapping(payload, "selection", "viewer_selection", "workspace_selection")

        has_mode = any(
            key in payload
            for key in (
                "mode",
                "workspace_mode",
                "viewer_mode",
            )
        ) or bool(nested_selection)

        patch: Dict[str, Any] = {}

        if has_mode:
            selection = _selection_payload(payload)
            patch["viewer_selection"] = selection
            patch["workspace_selection"] = selection
            patch["workspace_mode"] = selection.get("workspace_mode", DEFAULT_WORKSPACE_MODE)
            patch["mode"] = selection.get("mode", DEFAULT_MODE)

        _copy_optional_neutral_state(source=payload, target=patch)

        if "updated_at" not in patch and has_mode:
            try:
                import time

                patch["updated_at"] = int(time.time() * 1000)
            except Exception:
                pass

        if not patch:
            selection = _default_selection()
            patch["viewer_selection"] = selection
            patch["workspace_selection"] = selection
            patch["workspace_mode"] = DEFAULT_WORKSPACE_MODE
            patch["mode"] = DEFAULT_MODE

        return _json_safe(patch) if isinstance(_json_safe(patch), dict) else patch

    except Exception:
        selection = _default_selection()
        return {
            "viewer_selection": selection,
            "workspace_selection": selection,
            "workspace_mode": DEFAULT_WORKSPACE_MODE,
            "mode": DEFAULT_MODE,
        }


def _deep_merge_dict(base: Mapping[str, Any], patch: Mapping[str, Any], *, depth: int = 0) -> Dict[str, Any]:
    """
    Defensive recursive merge for the fallback path.

    ConversationState.merge_patch may be shallow in older states. This fallback
    keeps existing neutral side-state while replacing workspace selection.
    """
    if depth > _MAX_JSON_DEPTH:
        return dict(base or {})

    try:
        merged = dict(base or {})

        for key, value in dict(patch or {}).items():
            if _is_legacy_key(key):
                continue

            key_text = _clean_text(key, "", 160)
            if not key_text:
                continue

            existing = merged.get(key_text)

            if isinstance(existing, MappingABC) and isinstance(value, MappingABC):
                merged[key_text] = _deep_merge_dict(existing, value, depth=depth + 1)
            else:
                merged[key_text] = _json_safe(value)

        return merged

    except Exception:
        return dict(patch or {})


def _merge_state(chat_id: str, patch: Mapping[str, Any]) -> bool:
    try:
        ConversationState.merge_patch(str(chat_id), dict(patch))
        return True

    except Exception:
        _log_warning("ConversationState.merge_patch failed; trying fallback", exc_info=True)

    try:
        row = ConversationState.get_or_create(str(chat_id))
        current = _safe_state_dict(row)
        merged = _deep_merge_dict(current, patch)

        row.state_json = merged

        db.session.add(row)
        db.session.commit()

        return True

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

        _log_warning("ConversationState fallback merge failed", exc_info=True)
        return False


def _selection_response_payload(chat_id: str, selection: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        mode = _normalize_mode(selection.get("mode", DEFAULT_MODE))
        workspace_mode = _workspace_mode_for(mode)

        payload = {
            "ok": True,
            "chat_id": str(chat_id),
            "selection": dict(selection),
            "mode": mode,
            "workspace_mode": workspace_mode,
            "viewer_selection": dict(selection),
            "workspace_selection": dict(selection),
            "legacy_3d_backend": False,
            "legacy_speckle": False,
        }

        # Keep the top-level response friendly for main.js:
        # extractModeFromState() checks workspace_mode/mode first.
        payload.update(
            {
                "mode": mode,
                "workspace_mode": workspace_mode,
            }
        )

        return payload

    except Exception:
        selection_default = _default_selection()
        return {
            "ok": True,
            "chat_id": str(chat_id),
            "selection": selection_default,
            "mode": DEFAULT_MODE,
            "workspace_mode": DEFAULT_WORKSPACE_MODE,
            "viewer_selection": selection_default,
            "workspace_selection": selection_default,
            "legacy_3d_backend": False,
            "legacy_speckle": False,
        }


# ─────────────────────────────────────────────────────────────
# Route handlers
# ─────────────────────────────────────────────────────────────

def _handle_get_selection(chat_id: str) -> Response:
    conv = _get_conversation(chat_id)

    if not conv:
        return _json_error("not found", 404, code="chat_not_found")

    state = _get_state(str(chat_id))
    selection = _extract_selection_from_state(state)
    selection = _merge_optional_state_into_selection(state=state, selection=selection)

    return _json_response(_selection_response_payload(str(chat_id), selection), status=200)


def _handle_put_selection(chat_id: str) -> Response:
    conv = _get_conversation(chat_id)

    if not conv:
        return _json_error("not found", 404, code="chat_not_found")

    data = _request_json()
    patch = _state_patch_from_payload(data)

    saved = _merge_state(str(chat_id), patch)

    if not saved:
        return _json_error("state update failed", 500, code="state_update_failed")

    selection = _extract_selection_from_state(patch)

    return _json_response(_selection_response_payload(str(chat_id), selection), status=200)


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@bp.route(
    "/v1/chats/<chat_id>/viewer/selection",
    methods=["GET", "HEAD", "PUT", "PATCH"],
    endpoint="viewer_selection",
)
def viewer_selection(chat_id: str) -> Response:
    """
    Neutral workspace selection endpoint.

    The URL remains `/viewer/selection` for frontend compatibility.
    It no longer persists old project/model/version/viewer/Speckle fields.

    Accepted examples:
    - {"mode": "editor"}
    - {"mode": "map"}
    - {"workspace_mode": "2d"}
    - {"mode": "lv"}
    - {"mode": "admin"}
    - {"last_2d_selection": [...], "last_2d_selection_ts": 123}
    """
    method = str(request.method or "GET").upper()

    if method == "HEAD":
        conv = _get_conversation(chat_id)
        if not conv:
            return _empty_response(404)
        return _empty_response(204)

    if method == "GET":
        return _handle_get_selection(chat_id)

    if method in {"PUT", "PATCH"}:
        return _handle_put_selection(chat_id)

    return _json_error("method not allowed", 405, code="method_not_allowed")


# Compatibility aliases for direct imports in older code.
get_selection = viewer_selection
put_selection = viewer_selection