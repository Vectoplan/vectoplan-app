# services/vectoplan-app/services/ui_notice_service.py
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


try:
    from flask import current_app, has_app_context, has_request_context, request
except Exception:  # pragma: no cover - service must also be importable outside Flask
    current_app = None  # type: ignore[assignment]

    def has_app_context() -> bool:  # type: ignore[override]
        return False

    def has_request_context() -> bool:  # type: ignore[override]
        return False

    request = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


DEFAULT_CACHE_TTL_SECONDS = 30
DEFAULT_KIND = "demo"
DEFAULT_SEVERITY = "warning"
DEFAULT_SEPARATOR = "  ·  "
DEFAULT_SOURCE = "ui_notice_service"

DEFAULT_DEMO_LABEL = "DEMO"
DEFAULT_DEMO_TITLE = "Demo-Modus"
DEFAULT_DEMO_MESSAGES: Tuple[str, ...] = (
    "Du befindest dich im Demo-Modus.",
    "Änderungen werden nur temporär gespeichert.",
    "Keine dauerhafte Projektspeicherung.",
    "Keine echten Team-Einladungen.",
    "Kein Zugriff auf Bigdata-/Abo-Datenquellen.",
    "Kein echter Account-Kontext.",
)

DEFAULT_PUBLIC_LABEL = "ÖFFENTLICH"
DEFAULT_PUBLIC_TITLE = "Öffentliche Ansicht"
DEFAULT_PUBLIC_MESSAGES: Tuple[str, ...] = (
    "Du betrachtest ein öffentlich freigegebenes Projekt.",
    "Diese Ansicht ist schreibgeschützt.",
    "Team-, Admin- und Veröffentlichungseinstellungen sind nicht öffentlich verfügbar.",
)

TRUTHY_VALUES = {"1", "true", "yes", "y", "on", "enabled", "active"}
FALSY_VALUES = {"0", "false", "no", "n", "off", "disabled", "inactive", ""}

SENSITIVE_KEY_PARTS = (
    "cookie",
    "authorization",
    "token",
    "secret",
    "password",
    "session",
    "api_key",
    "apikey",
    "bearer",
)


@dataclass(frozen=True)
class NoticeState:
    demo_mode: bool = False
    public_viewer: bool = False
    read_only: bool = False
    auth_unavailable: bool = False
    user_blocked: bool = False
    access_blocked: bool = False
    authenticated: bool = False
    access_mode: str = ""
    locale: str = "de"
    request_path: str = ""
    demo_expires_at: str = ""
    project_public_id: str = ""


class _TTLCache:
    def __init__(self) -> None:
        self._items: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            now = time.monotonic()
            with self._lock:
                item = self._items.get(key)
                if not item:
                    return None

                expires_at, value = item
                if expires_at <= now:
                    self._items.pop(key, None)
                    return None

                return copy.deepcopy(value)
        except Exception:
            logger.exception("ui_notice_service.cache_get_failed")
            return None

    def set(self, key: str, value: Mapping[str, Any], ttl_seconds: int) -> None:
        try:
            if ttl_seconds <= 0:
                return

            expires_at = time.monotonic() + ttl_seconds
            with self._lock:
                self._items[key] = (expires_at, copy.deepcopy(dict(value)))
                self._prune_locked()
        except Exception:
            logger.exception("ui_notice_service.cache_set_failed")

    def clear(self) -> int:
        try:
            with self._lock:
                count = len(self._items)
                self._items.clear()
                return count
        except Exception:
            logger.exception("ui_notice_service.cache_clear_failed")
            return 0

    def status(self) -> Dict[str, Any]:
        try:
            with self._lock:
                self._prune_locked()
                return {
                    "ok": True,
                    "items": len(self._items),
                    "ttl_default_seconds": DEFAULT_CACHE_TTL_SECONDS,
                }
        except Exception as exc:
            logger.exception("ui_notice_service.cache_status_failed")
            return {
                "ok": False,
                "items": None,
                "error": _safe_error(exc),
            }

    def _prune_locked(self) -> None:
        try:
            now = time.monotonic()
            expired = [key for key, (expires_at, _) in self._items.items() if expires_at <= now]
            for key in expired:
                self._items.pop(key, None)
        except Exception:
            logger.exception("ui_notice_service.cache_prune_failed")


_CACHE = _TTLCache()


def build_notice_stream(
    current_user_context: Any = None,
    *,
    access_context: Any = None,
    project: Any = None,
    explicit_notice: Optional[Mapping[str, Any]] = None,
    extra_messages: Optional[Sequence[Any]] = None,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Build a backend-driven, template-safe notice stream payload.

    This service intentionally returns plain data only. It does not build HTML.
    Routes should pass the returned dict into a Jinja template, for example:

        notice_stream = build_notice_stream(current_user_context=ctx)
        return render_template("chat_viewer.html", notice_stream=notice_stream, ...)

    The template should escape all text values normally.
    """

    try:
        if not _config_bool("UI_NOTICE_ENABLED", True):
            return _disabled_notice("globally_disabled")

        state = _extract_notice_state(
            current_user_context=current_user_context,
            access_context=access_context,
            project=project,
        )

        cache_key = _build_cache_key(
            state=state,
            explicit_notice=explicit_notice,
            extra_messages=extra_messages,
        )

        if not force_refresh:
            cached = _CACHE.get(cache_key)
            if cached is not None:
                return cached

        notice = _build_notice_payload(
            state=state,
            explicit_notice=explicit_notice,
            extra_messages=extra_messages,
        )

        ttl_seconds = _cache_ttl_seconds()
        _CACHE.set(cache_key, notice, ttl_seconds)
        return copy.deepcopy(notice)
    except Exception as exc:
        logger.exception("ui_notice_service.build_notice_stream_failed")
        return _disabled_notice(
            "build_failed",
            metadata={
                "error": _safe_error(exc),
            },
        )


def build_demo_notice_stream(
    *,
    extra_messages: Optional[Sequence[Any]] = None,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    try:
        state = NoticeState(demo_mode=True, access_mode="demo")
        cache_key = _build_cache_key(
            state=state,
            explicit_notice=None,
            extra_messages=extra_messages,
            prefix="demo",
        )

        if not force_refresh:
            cached = _CACHE.get(cache_key)
            if cached is not None:
                return cached

        notice = _build_demo_notice(state=state, extra_messages=extra_messages)
        _CACHE.set(cache_key, notice, _cache_ttl_seconds())
        return copy.deepcopy(notice)
    except Exception as exc:
        logger.exception("ui_notice_service.build_demo_notice_stream_failed")
        return _disabled_notice(
            "demo_build_failed",
            metadata={
                "error": _safe_error(exc),
            },
        )


def clear_ui_notice_cache() -> Dict[str, Any]:
    cleared = _CACHE.clear()
    return {
        "ok": True,
        "cleared": cleared,
        "source": DEFAULT_SOURCE,
    }


def get_ui_notice_cache_status() -> Dict[str, Any]:
    status = _CACHE.status()
    status["source"] = DEFAULT_SOURCE
    return status


def get_notice_stream_status() -> Dict[str, Any]:
    try:
        return {
            "ok": True,
            "source": DEFAULT_SOURCE,
            "enabled": _config_bool("UI_NOTICE_ENABLED", True),
            "demo_enabled": _config_bool("UI_NOTICE_DEMO_ENABLED", True),
            "public_enabled": _config_bool("UI_NOTICE_PUBLIC_ENABLED", False),
            "cache": get_ui_notice_cache_status(),
            "generated_at": _utcnow_iso(),
        }
    except Exception as exc:
        logger.exception("ui_notice_service.status_failed")
        return {
            "ok": False,
            "source": DEFAULT_SOURCE,
            "error": _safe_error(exc),
            "generated_at": _utcnow_iso(),
        }


def _build_notice_payload(
    *,
    state: NoticeState,
    explicit_notice: Optional[Mapping[str, Any]],
    extra_messages: Optional[Sequence[Any]],
) -> Dict[str, Any]:
    try:
        if explicit_notice:
            return _normalize_explicit_notice(explicit_notice, state=state, extra_messages=extra_messages)

        if state.demo_mode and _config_bool("UI_NOTICE_DEMO_ENABLED", True):
            return _build_demo_notice(state=state, extra_messages=extra_messages)

        if state.public_viewer and _config_bool("UI_NOTICE_PUBLIC_ENABLED", False):
            return _build_public_notice(state=state, extra_messages=extra_messages)

        return _disabled_notice("no_active_notice", state=state)
    except Exception as exc:
        logger.exception("ui_notice_service.payload_failed")
        return _disabled_notice(
            "payload_failed",
            state=state,
            metadata={
                "error": _safe_error(exc),
            },
        )


def _build_demo_notice(
    *,
    state: NoticeState,
    extra_messages: Optional[Sequence[Any]],
) -> Dict[str, Any]:
    label = _config_text("UI_NOTICE_DEMO_LABEL", DEFAULT_DEMO_LABEL)
    title = _config_text("UI_NOTICE_DEMO_TITLE", DEFAULT_DEMO_TITLE)

    messages = _configured_messages(
        config_key="UI_NOTICE_DEMO_MESSAGES",
        json_config_key="UI_NOTICE_DEMO_MESSAGES_JSON",
        default_messages=DEFAULT_DEMO_MESSAGES,
    )
    messages = _merge_messages(messages, extra_messages)

    return _notice_payload(
        enabled=True,
        kind="demo",
        severity=_config_text("UI_NOTICE_DEMO_SEVERITY", DEFAULT_SEVERITY),
        label=label,
        title=title,
        messages=messages,
        state=state,
        dismissible=_config_bool("UI_NOTICE_DEMO_DISMISSIBLE", False),
        metadata={
            "demo_mode": True,
            "demo_expires_at": state.demo_expires_at,
        },
    )


def _build_public_notice(
    *,
    state: NoticeState,
    extra_messages: Optional[Sequence[Any]],
) -> Dict[str, Any]:
    label = _config_text("UI_NOTICE_PUBLIC_LABEL", DEFAULT_PUBLIC_LABEL)
    title = _config_text("UI_NOTICE_PUBLIC_TITLE", DEFAULT_PUBLIC_TITLE)

    messages = _configured_messages(
        config_key="UI_NOTICE_PUBLIC_MESSAGES",
        json_config_key="UI_NOTICE_PUBLIC_MESSAGES_JSON",
        default_messages=DEFAULT_PUBLIC_MESSAGES,
    )
    messages = _merge_messages(messages, extra_messages)

    return _notice_payload(
        enabled=True,
        kind="public",
        severity=_config_text("UI_NOTICE_PUBLIC_SEVERITY", "info"),
        label=label,
        title=title,
        messages=messages,
        state=state,
        dismissible=_config_bool("UI_NOTICE_PUBLIC_DISMISSIBLE", False),
        metadata={
            "public_viewer": True,
            "read_only": True,
            "project_public_id": state.project_public_id,
        },
    )


def _normalize_explicit_notice(
    explicit_notice: Mapping[str, Any],
    *,
    state: NoticeState,
    extra_messages: Optional[Sequence[Any]],
) -> Dict[str, Any]:
    try:
        enabled = _as_bool(explicit_notice.get("enabled", True), True)
        if not enabled:
            return _disabled_notice("explicit_notice_disabled", state=state)

        kind = _clean_token(explicit_notice.get("kind"), fallback=DEFAULT_KIND)
        severity = _clean_token(explicit_notice.get("severity"), fallback=DEFAULT_SEVERITY)
        label = _clean_text(explicit_notice.get("label"), fallback=kind.upper(), max_len=80)
        title = _clean_text(explicit_notice.get("title"), fallback=label, max_len=160)

        raw_messages = explicit_notice.get("messages")
        if raw_messages is None:
            raw_messages = explicit_notice.get("message")
        if raw_messages is None:
            raw_messages = explicit_notice.get("text")

        messages = _coerce_messages(raw_messages)
        messages = _merge_messages(messages, extra_messages)

        if not messages:
            messages = [title]

        dismissible = _as_bool(explicit_notice.get("dismissible", False), False)
        metadata = _sanitize_metadata(explicit_notice.get("metadata"))

        return _notice_payload(
            enabled=True,
            kind=kind,
            severity=severity,
            label=label,
            title=title,
            messages=messages,
            state=state,
            dismissible=dismissible,
            metadata=metadata,
        )
    except Exception as exc:
        logger.exception("ui_notice_service.explicit_notice_normalization_failed")
        return _disabled_notice(
            "explicit_notice_invalid",
            state=state,
            metadata={
                "error": _safe_error(exc),
            },
        )


def _notice_payload(
    *,
    enabled: bool,
    kind: str,
    severity: str,
    label: str,
    title: str,
    messages: Sequence[str],
    state: NoticeState,
    dismissible: bool,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    safe_messages = _merge_messages(messages, None)
    separator = _config_text("UI_NOTICE_SEPARATOR", DEFAULT_SEPARATOR)
    marquee_text = _build_marquee_text(label=label, messages=safe_messages, separator=separator)

    payload = {
        "ok": True,
        "enabled": bool(enabled and safe_messages),
        "kind": _clean_token(kind, fallback=DEFAULT_KIND),
        "severity": _clean_token(severity, fallback=DEFAULT_SEVERITY),
        "label": _clean_text(label, fallback=DEFAULT_DEMO_LABEL, max_len=80),
        "title": _clean_text(title, fallback=DEFAULT_DEMO_TITLE, max_len=160),
        "messages": safe_messages,
        "marquee_text": marquee_text,
        "separator": separator,
        "dismissible": bool(dismissible),
        "aria_label": _build_aria_label(label=label, title=title, messages=safe_messages),
        "access_mode": state.access_mode,
        "demo_mode": state.demo_mode,
        "public_viewer": state.public_viewer,
        "read_only": state.read_only,
        "generated_at": _utcnow_iso(),
        "source": DEFAULT_SOURCE,
        "metadata": _sanitize_metadata(
            {
                "request_path": state.request_path,
                "project_public_id": state.project_public_id,
                "locale": state.locale,
                **dict(metadata or {}),
            }
        ),
    }

    return payload


def _disabled_notice(
    reason: str,
    *,
    state: Optional[NoticeState] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    safe_state = state or NoticeState()

    return {
        "ok": True,
        "enabled": False,
        "kind": "",
        "severity": "",
        "label": "",
        "title": "",
        "messages": [],
        "marquee_text": "",
        "separator": _config_text("UI_NOTICE_SEPARATOR", DEFAULT_SEPARATOR),
        "dismissible": False,
        "aria_label": "",
        "access_mode": safe_state.access_mode,
        "demo_mode": safe_state.demo_mode,
        "public_viewer": safe_state.public_viewer,
        "read_only": safe_state.read_only,
        "generated_at": _utcnow_iso(),
        "source": DEFAULT_SOURCE,
        "reason": _clean_token(reason, fallback="disabled"),
        "metadata": _sanitize_metadata(metadata or {}),
    }


def _extract_notice_state(
    *,
    current_user_context: Any,
    access_context: Any,
    project: Any,
) -> NoticeState:
    try:
        demo_mode = _context_bool(
            current_user_context,
            access_context,
            keys=(
                "demo_mode",
                "is_demo",
                "is_demo_mode",
                "current_user_demo",
                "demo",
            ),
            default=False,
        )

        public_viewer = _context_bool(
            access_context,
            current_user_context,
            keys=(
                "public_viewer",
                "is_public_viewer",
                "anonymous_public_viewer",
                "public_access",
            ),
            default=False,
        )

        read_only = _context_bool(
            access_context,
            current_user_context,
            keys=(
                "read_only",
                "readonly",
                "is_read_only",
            ),
            default=public_viewer,
        )

        auth_unavailable = _context_bool(
            current_user_context,
            access_context,
            keys=(
                "auth_unavailable",
                "service_unavailable",
                "auth_service_unavailable",
            ),
            default=False,
        )

        user_blocked = _context_bool(
            current_user_context,
            access_context,
            keys=(
                "user_blocked",
                "blocked_user",
                "is_blocked",
                "banned",
            ),
            default=False,
        )

        access_blocked = _context_bool(
            current_user_context,
            access_context,
            keys=(
                "access_blocked",
                "blocked",
                "denied",
            ),
            default=False,
        )

        authenticated = _context_bool(
            current_user_context,
            access_context,
            keys=(
                "authenticated",
                "is_authenticated",
                "logged_in",
            ),
            default=False,
        )

        access_mode = _first_text(
            _safe_get(access_context, "access_mode"),
            _safe_get(access_context, "mode"),
            _safe_get(current_user_context, "access_mode"),
            "demo" if demo_mode else "",
            "public" if public_viewer else "",
            "authenticated" if authenticated else "",
        )

        locale = _first_text(
            _safe_get(access_context, "locale"),
            _safe_get(current_user_context, "locale"),
            _request_locale(),
            "de",
        )

        request_path = _request_path()
        demo_expires_at = _first_text(
            _safe_get(current_user_context, "demo_expires_at"),
            _safe_get(current_user_context, "expires_at"),
            _safe_get(access_context, "demo_expires_at"),
            "",
        )

        project_public_id = _first_text(
            _safe_get(project, "public_id"),
            _safe_get(project, "project_public_id"),
            _safe_get(access_context, "project_public_id"),
            _safe_get(access_context, "project_id"),
            "",
        )

        return NoticeState(
            demo_mode=demo_mode,
            public_viewer=public_viewer,
            read_only=read_only,
            auth_unavailable=auth_unavailable,
            user_blocked=user_blocked,
            access_blocked=access_blocked,
            authenticated=authenticated,
            access_mode=access_mode,
            locale=locale,
            request_path=request_path,
            demo_expires_at=demo_expires_at,
            project_public_id=project_public_id,
        )
    except Exception as exc:
        logger.exception("ui_notice_service.extract_state_failed")
        return NoticeState(
            demo_mode=False,
            access_mode="",
            request_path=_request_path(),
            locale="de",
            project_public_id="",
            demo_expires_at="",
            auth_unavailable=False,
            user_blocked=False,
            access_blocked=False,
            authenticated=False,
            public_viewer=False,
            read_only=False,
        )


def _build_cache_key(
    *,
    state: NoticeState,
    explicit_notice: Optional[Mapping[str, Any]],
    extra_messages: Optional[Sequence[Any]],
    prefix: str = "notice",
) -> str:
    try:
        relevant = {
            "prefix": prefix,
            "demo_mode": state.demo_mode,
            "public_viewer": state.public_viewer,
            "read_only": state.read_only,
            "access_mode": state.access_mode,
            "locale": state.locale,
            "demo_expires_at": state.demo_expires_at,
            "project_public_id": state.project_public_id,
            "explicit": _stable_json(_sanitize_metadata(explicit_notice or {})),
            "extra": _stable_json(_merge_messages([], extra_messages)),
            "config": {
                "enabled": _config_bool("UI_NOTICE_ENABLED", True),
                "demo_enabled": _config_bool("UI_NOTICE_DEMO_ENABLED", True),
                "public_enabled": _config_bool("UI_NOTICE_PUBLIC_ENABLED", False),
                "separator": _config_text("UI_NOTICE_SEPARATOR", DEFAULT_SEPARATOR),
                "demo_label": _config_text("UI_NOTICE_DEMO_LABEL", DEFAULT_DEMO_LABEL),
                "demo_title": _config_text("UI_NOTICE_DEMO_TITLE", DEFAULT_DEMO_TITLE),
                "demo_messages": _config_text("UI_NOTICE_DEMO_MESSAGES", ""),
                "public_messages": _config_text("UI_NOTICE_PUBLIC_MESSAGES", ""),
            },
        }
        return "ui_notice:" + _stable_json(relevant)
    except Exception:
        logger.exception("ui_notice_service.cache_key_failed")
        return f"ui_notice:fallback:{time.monotonic()}"


def _cache_ttl_seconds() -> int:
    value = _config_value("UI_NOTICE_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS)
    try:
        ttl = int(value)
        if ttl < 0:
            return DEFAULT_CACHE_TTL_SECONDS
        return min(ttl, 3600)
    except Exception:
        return DEFAULT_CACHE_TTL_SECONDS


def _configured_messages(
    *,
    config_key: str,
    json_config_key: str,
    default_messages: Sequence[str],
) -> List[str]:
    try:
        raw_json = _config_value(json_config_key, None)
        json_messages = _messages_from_json(raw_json)
        if json_messages:
            return json_messages

        raw_text = _config_value(config_key, None)
        text_messages = _messages_from_text(raw_text)
        if text_messages:
            return text_messages

        return _merge_messages(default_messages, None)
    except Exception:
        logger.exception("ui_notice_service.configured_messages_failed")
        return _merge_messages(default_messages, None)


def _messages_from_json(raw_value: Any) -> List[str]:
    try:
        if raw_value is None:
            return []

        if isinstance(raw_value, (list, tuple)):
            return _merge_messages(raw_value, None)

        if isinstance(raw_value, str):
            stripped = raw_value.strip()
            if not stripped:
                return []

            loaded = json.loads(stripped)
            return _merge_messages(loaded, None)

        return []
    except Exception:
        logger.warning("ui_notice_service.invalid_json_messages", exc_info=True)
        return []


def _messages_from_text(raw_value: Any) -> List[str]:
    try:
        if raw_value is None:
            return []

        if isinstance(raw_value, (list, tuple)):
            return _merge_messages(raw_value, None)

        text = str(raw_value).strip()
        if not text:
            return []

        if "|" in text:
            return _merge_messages([part.strip() for part in text.split("|")], None)

        if "\n" in text:
            return _merge_messages([part.strip() for part in text.splitlines()], None)

        return _merge_messages([text], None)
    except Exception:
        logger.warning("ui_notice_service.invalid_text_messages", exc_info=True)
        return []


def _merge_messages(
    base_messages: Optional[Sequence[Any]],
    extra_messages: Optional[Sequence[Any]],
) -> List[str]:
    result: List[str] = []
    seen = set()

    for raw in list(base_messages or []) + list(extra_messages or []):
        text = _clean_text(raw, fallback="", max_len=500)
        if not text:
            continue

        key = text.lower()
        if key in seen:
            continue

        seen.add(key)
        result.append(text)

    return result


def _coerce_messages(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, str):
        return _messages_from_text(value)

    if isinstance(value, (list, tuple, set)):
        return _merge_messages(list(value), None)

    return _merge_messages([value], None)


def _build_marquee_text(*, label: str, messages: Sequence[str], separator: str) -> str:
    try:
        safe_label = _clean_text(label, fallback="", max_len=80)
        safe_messages = _merge_messages(messages, None)

        parts: List[str] = []
        if safe_label:
            parts.append(safe_label)
        parts.extend(safe_messages)

        return separator.join(parts).strip()
    except Exception:
        logger.exception("ui_notice_service.marquee_text_failed")
        return ""


def _build_aria_label(*, label: str, title: str, messages: Sequence[str]) -> str:
    try:
        safe_label = _clean_text(label, fallback="", max_len=80)
        safe_title = _clean_text(title, fallback="", max_len=160)
        safe_messages = _merge_messages(messages, None)

        parts = [part for part in [safe_label, safe_title] + safe_messages if part]
        return ". ".join(parts)
    except Exception:
        logger.exception("ui_notice_service.aria_label_failed")
        return ""


def _context_bool(*contexts: Any, keys: Sequence[str], default: bool = False) -> bool:
    try:
        for context in contexts:
            for key in keys:
                value = _safe_get(context, key, default=None)
                if value is not None:
                    return _as_bool(value, default)
        return default
    except Exception:
        logger.exception("ui_notice_service.context_bool_failed")
        return default


def _safe_get(source: Any, key: str, default: Any = None) -> Any:
    try:
        if source is None or not key:
            return default

        if isinstance(source, Mapping):
            if key in source:
                return source.get(key, default)

            # common nested aliases
            for nested_key in ("current_user", "user", "auth", "context", "access", "project"):
                nested = source.get(nested_key)
                if isinstance(nested, Mapping) and key in nested:
                    return nested.get(key, default)

            return default

        if hasattr(source, key):
            value = getattr(source, key)
            if callable(value):
                return default
            return value

        for nested_key in ("current_user", "user", "auth", "context", "access", "project"):
            if hasattr(source, nested_key):
                nested = getattr(source, nested_key)
                if isinstance(nested, Mapping) and key in nested:
                    return nested.get(key, default)
                if nested is not None and hasattr(nested, key):
                    value = getattr(nested, key)
                    if not callable(value):
                        return value

        return default
    except Exception:
        logger.debug("ui_notice_service.safe_get_failed key=%s", key, exc_info=True)
        return default


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value, fallback="", max_len=500)
        if text:
            return text
    return ""


def _as_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        if value is None:
            return default

        if isinstance(value, (int, float)):
            return bool(value)

        text = str(value).strip().lower()
        if text in TRUTHY_VALUES:
            return True
        if text in FALSY_VALUES:
            return False

        return default
    except Exception:
        return default


def _config_bool(key: str, default: bool) -> bool:
    return _as_bool(_config_value(key, default), default)


def _config_text(key: str, default: str) -> str:
    return _clean_text(_config_value(key, default), fallback=default, max_len=1000)


def _config_value(key: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None:
            value = current_app.config.get(key)
            if value is not None:
                return value

        value = os.environ.get(key)
        if value is not None:
            return value

        return default
    except Exception:
        logger.debug("ui_notice_service.config_value_failed key=%s", key, exc_info=True)
        return default


def _request_path() -> str:
    try:
        if has_request_context() and request is not None:
            return _clean_text(getattr(request, "path", ""), fallback="", max_len=500)
    except Exception:
        logger.debug("ui_notice_service.request_path_failed", exc_info=True)
    return ""


def _request_locale() -> str:
    try:
        if has_request_context() and request is not None:
            accept_languages = getattr(request, "accept_languages", None)
            if accept_languages is not None:
                best = accept_languages.best_match(["de", "en"])
                if best:
                    return best
    except Exception:
        logger.debug("ui_notice_service.request_locale_failed", exc_info=True)
    return "de"


def _clean_text(value: Any, *, fallback: str = "", max_len: int = 500) -> str:
    try:
        if value is None:
            return fallback

        text = str(value)
        text = text.replace("\x00", "")
        text = " ".join(text.split())
        text = text.strip()

        if not text:
            return fallback

        if max_len > 0 and len(text) > max_len:
            text = text[: max_len - 1].rstrip() + "…"

        return text
    except Exception:
        return fallback


def _clean_token(value: Any, *, fallback: str) -> str:
    try:
        text = _clean_text(value, fallback=fallback, max_len=80).lower()
        allowed = []
        for char in text:
            if char.isalnum() or char in ("_", "-"):
                allowed.append(char)
        cleaned = "".join(allowed).strip("_-")
        return cleaned or fallback
    except Exception:
        return fallback


def _sanitize_metadata(value: Any) -> Dict[str, Any]:
    try:
        if value is None:
            return {}

        if not isinstance(value, Mapping):
            return {
                "value": _clean_text(value, fallback="", max_len=300),
            }

        result: Dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _clean_text(raw_key, fallback="", max_len=120)
            if not key:
                continue

            lower_key = key.lower()
            if any(part in lower_key for part in SENSITIVE_KEY_PARTS):
                result[key] = "[redacted]"
                continue

            if isinstance(raw_value, Mapping):
                result[key] = _sanitize_metadata(raw_value)
            elif isinstance(raw_value, (list, tuple, set)):
                result[key] = [
                    _clean_text(item, fallback="", max_len=300)
                    for item in list(raw_value)[:20]
                ]
            elif isinstance(raw_value, (bool, int, float)) or raw_value is None:
                result[key] = raw_value
            else:
                result[key] = _clean_text(raw_value, fallback="", max_len=500)

        return result
    except Exception:
        logger.debug("ui_notice_service.metadata_sanitize_failed", exc_info=True)
        return {}


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return str(value)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(exc: BaseException) -> str:
    try:
        return f"{exc.__class__.__name__}: {_clean_text(str(exc), fallback='error', max_len=300)}"
    except Exception:
        return "error"


__all__ = [
    "build_notice_stream",
    "build_demo_notice_stream",
    "clear_ui_notice_cache",
    "get_ui_notice_cache_status",
    "get_notice_stream_status",
]