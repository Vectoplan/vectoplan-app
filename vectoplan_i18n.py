"""Small Flask adapter for the central vectoplan-language service."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen  # nosec B310 - configured internal service

from flask import current_app, g, request


log = logging.getLogger(__name__)

SUPPORTED = (
    "en", "de", "fr", "es", "pt", "ru", "zh-CN", "ja", "ko",
    "tr", "pl", "it", "nl", "ar", "id", "cs", "uk", "sv",
)
RTL = {"ar"}

_MARKED = re.compile(
    r'(<[^>]*\sdata-i18n\s*=\s*"([^"]+)"[^>]*>)([^<]*)',
    re.IGNORECASE,
)
_OPENING_TAG = re.compile(r"<[a-zA-Z][^>]*>", re.DOTALL)
_ATTRIBUTE_MARKER = re.compile(
    r'\sdata-i18n-(placeholder|title|aria-label|alt)\s*=\s*(["\'])(.*?)\2',
    re.IGNORECASE | re.DOTALL,
)
_BUNDLED = re.compile(
    r'<span[^>]*\sdata-vp-i18n\s*=\s*"(\d+)"[^>]*>([^<]*)</span>',
    re.IGNORECASE,
)
_HTML_LANG = re.compile(r'(<html\b[^>]*\blang\s*=\s*)["\'][^"\']*["\']', re.IGNORECASE)


def _config(app, name: str, default):
    value = app.config.get(name) if name in app.config else None
    return os.environ.get(name, default) if value is None else value


def _bool(app, name: str, default: bool) -> bool:
    value = str(_config(app, name, default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _canon(value) -> str:
    raw = str(value or "").strip().replace("_", "-")
    lowered = raw.lower()
    if lowered in {"zh", "zh-cn", "zh-hans"}:
        return "zh-CN"
    if lowered in {"zh-tw", "zh-hant", "zh-hk"}:
        return "zh-TW"
    base = lowered.split("-", 1)[0]
    return base if base in SUPPORTED else lowered


def _supported(app) -> tuple[str, ...]:
    configured = str(_config(app, "APP_LANGS", ",".join(SUPPORTED)))
    values = tuple(dict.fromkeys(_canon(item) for item in configured.split(",") if item.strip()))
    return tuple(item for item in values if item in SUPPORTED) or SUPPORTED


def _from_auth_context(supported: tuple[str, ...]):
    try:
        context = getattr(g, "vectoplan_auth_language", None)
        if isinstance(context, Mapping):
            for key in ("language", "preferred_language", "detected_language"):
                candidate = _canon(context.get(key))
                if candidate in supported:
                    return candidate

        user = getattr(g, "current_user", None)
        if isinstance(user, Mapping):
            values = (user.get("language"), user.get("locale"))
        else:
            values = (getattr(user, "language", None), getattr(user, "locale", None))
        for value in values:
            candidate = _canon(value)
            if candidate in supported:
                return candidate
    except Exception:
        pass
    return None


def resolve_language(app) -> str:
    supported = _supported(app)
    # Explicit request propagation and browser choice must win over a stale
    # Auth context from the request that performed the language switch.
    for header in ("X-Vectoplan-Language", "X-Vectoplan-Client-Locale"):
        candidate = _canon(request.headers.get(header, ""))
        if candidate in supported:
            return candidate

    cookie_name = str(_config(app, "APP_LANG_COOKIE_NAME", "vectoplan_language"))
    for name in dict.fromkeys((cookie_name, "vectoplan_language", "ui_lang")):
        candidate = _canon(request.cookies.get(name, ""))
        if candidate in supported:
            return candidate

    candidate = _from_auth_context(supported)
    if candidate:
        return candidate

    for item in request.headers.get("Accept-Language", "").split(","):
        candidate = _canon(item.split(";", 1)[0])
        if candidate in supported:
            return candidate

    default = _canon(_config(app, "APP_DEFAULT_LANG", "de"))
    return default if default in supported else supported[0]


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    req = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=max(1, timeout)) as response:  # nosec B310
        value = json.loads(response.read().decode("utf-8", errors="replace"))
    return value if isinstance(value, dict) else {}


def _chunks(items: list[tuple[str, str]], maximum: int):
    batch: list[tuple[str, str]] = []
    size = 0
    for item in items:
        estimated = len(item[0]) + len(item[1]) + 80
        if batch and size + estimated > maximum:
            yield batch
            batch = []
            size = 0
        batch.append(item)
        size += estimated
    if batch:
        yield batch


def _runtime(app) -> dict:
    return app.extensions.setdefault(
        "vectoplan_i18n",
        {"translations": {}, "last_error": 0.0, "initialized": False},
    )


def _marked_pairs(rendered: str):
    for match in _MARKED.finditer(rendered or ""):
        yield match.group(2), match.group(3)

    for tag_match in _OPENING_TAG.finditer(rendered or ""):
        opening = tag_match.group(0)
        for marker in _ATTRIBUTE_MARKER.finditer(opening):
            attribute = marker.group(1)
            key = marker.group(3)
            value_match = re.search(
                rf'(?<![\w:-]){re.escape(attribute)}\s*=\s*(["\'])(.*?)\1',
                opening,
                re.IGNORECASE | re.DOTALL,
            )
            if value_match is not None:
                yield key, value_match.group(2)


def _missing(rendered: str, lang: str, cache: dict) -> list[tuple[str, str]]:
    existing = cache.setdefault(lang, {})
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_key, raw_fallback in _marked_pairs(rendered):
        key = html.unescape((raw_key or "").strip())
        fallback = html.unescape((raw_fallback or "").strip())
        pair = (key, fallback)
        if not key or not fallback or key in existing or pair in seen:
            continue
        if not any(character.isalpha() for character in fallback):
            continue
        seen.add(pair)
        result.append(pair)
    return result


def _hydrate(app, rendered: str, lang: str) -> None:
    extension = _runtime(app)
    cache = extension.setdefault("translations", {})
    source = _canon(_config(app, "APP_I18N_SOURCE_LANG", _config(app, "APP_DEFAULT_LANG", "de")))
    if lang == source:
        return
    missing = _missing(rendered, lang, cache)
    if not missing:
        return

    url = str(_config(app, "TR_SVC_BASE_URL", "http://vectoplan-language:8000")).rstrip("/")
    timeout = int(_config(app, "TR_SVC_TIMEOUT_SEC", 15))
    maximum = max(512, int(_config(app, "APP_I18N_LIVE_BATCH_CHARS", 8000)))
    target_cache = cache.setdefault(lang, {})

    for batch in _chunks(missing, maximum):
        bundle = "".join(
            f'<span data-vp-i18n="{index}">{html.escape(text)}</span>'
            for index, (_key, text) in enumerate(batch)
        )
        try:
            response = _post_json(
                f"{url}/translate",
                {
                    "text": bundle,
                    "source": source,
                    "targets": [lang],
                    "format": "html",
                    "alternatives": 0,
                },
                timeout,
            )
            translations = response.get("translations")
            translated = translations.get(lang) if isinstance(translations, Mapping) else None
            if not translated:
                continue
            values = {
                int(match.group(1)): html.unescape(match.group(2) or "")
                for match in _BUNDLED.finditer(str(translated))
            }
            for index, (key, _fallback) in enumerate(batch):
                if index in values:
                    target_cache[key] = values[index]
        except (HTTPError, URLError, OSError, ValueError, TypeError) as exc:
            now = time.monotonic()
            if now - float(extension.get("last_error", 0.0)) > 60:
                log.warning("vectoplan-language unavailable: %s", type(exc).__name__)
                extension["last_error"] = now
            break


def _transform(app, rendered: str, lang: str) -> str:
    cache = _runtime(app).setdefault("translations", {}).setdefault(lang, {})

    def replace(match: re.Match[str]) -> str:
        opening, key, original = match.group(1), match.group(2), match.group(3)
        fallback = original.strip()
        translated = cache.get(key, fallback)
        if not fallback:
            return opening + original
        leading = original[: len(original) - len(original.lstrip())]
        trailing = original[len(original.rstrip()) :]
        return opening + leading + translated + trailing

    def replace_opening(match: re.Match[str]) -> str:
        opening = match.group(0)
        updated_opening = opening
        for marker in _ATTRIBUTE_MARKER.finditer(opening):
            attribute = marker.group(1)
            key = html.unescape((marker.group(3) or "").strip())
            value_match = re.search(
                rf'(?<![\w:-]){re.escape(attribute)}\s*=\s*(["\'])(.*?)\1',
                updated_opening,
                re.IGNORECASE | re.DOTALL,
            )
            if value_match is None:
                continue
            fallback = html.unescape(value_match.group(2) or "")
            translated = cache.get(key, fallback)
            escaped = html.escape(translated or fallback, quote=True)
            updated_opening = (
                updated_opening[: value_match.start(2)]
                + escaped
                + updated_opening[value_match.end(2) :]
            )
        return updated_opening

    updated = _MARKED.sub(replace, rendered)
    updated = _OPENING_TAG.sub(replace_opening, updated)
    return _HTML_LANG.sub(lambda match: match.group(1) + f'"{lang}"', updated, count=1)


def _after_request(response):
    app = current_app
    try:
        if not _bool(app, "APP_I18N_TRANSFORM_HTML", True):
            return response
        if not 200 <= int(response.status_code) < 300:
            return response
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            return response

        lang = resolve_language(app)
        rendered = response.get_data(as_text=True)
        if _bool(app, "APP_I18N_LIVE_TRANSLATE", True):
            _hydrate(app, rendered, lang)
        response.set_data(_transform(app, rendered, lang))
        response.headers["Content-Language"] = lang
        response.headers.setdefault("Vary", "Accept-Language, Cookie")
    except Exception as exc:
        log.debug("i18n response transform failed: %r", exc)
    return response


def _template_context():
    lang = resolve_language(current_app)
    return {"ui_lang": lang, "is_rtl": lang in RTL}


def translate(key: str, fallback: str = "", lang: str | None = None) -> str:
    """Translate text that cannot legally contain a marker element (e.g. title)."""
    app = current_app
    selected = _canon(lang) if lang else resolve_language(app)
    source = _canon(_config(app, "APP_I18N_SOURCE_LANG", _config(app, "APP_DEFAULT_LANG", "de")))
    if selected == source or not key or not fallback:
        return fallback

    cache = _runtime(app).setdefault("translations", {}).setdefault(selected, {})
    if key not in cache:
        marker = f'<span data-i18n="{html.escape(key, quote=True)}">{html.escape(fallback)}</span>'
        _hydrate(app, marker, selected)
    return cache.get(key, fallback)


def init_app(app) -> None:
    extension = _runtime(app)
    if extension.get("initialized"):
        return
    app.after_request(_after_request)
    app.context_processor(_template_context)
    app.jinja_env.globals.setdefault("t", translate)
    extension["initialized"] = True
