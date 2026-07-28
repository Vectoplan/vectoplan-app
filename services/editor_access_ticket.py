"""Short-lived, signed App -> Editor project access tickets."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Mapping

try:
    from flask import current_app, has_app_context
except Exception:  # pragma: no cover
    current_app = None  # type: ignore[assignment]

    def has_app_context() -> bool:
        return False


TICKET_VERSION = 1
TICKET_ISSUER = "vectoplan-app"
TICKET_AUDIENCE = "vectoplan-editor"
DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 900


class EditorAccessTicketError(RuntimeError):
    pass


def _config_value(name: str, default: Any = None) -> Any:
    if has_app_context() and current_app is not None:
        value = current_app.config.get(name)
        if value not in {None, ""}:
            return value
    return os.getenv(name, default)


def _secret() -> bytes:
    value = _config_value("VECTOPLAN_EDITOR_ACCESS_TICKET_SECRET", "")
    if not value:
        value = _config_value("SECRET_KEY", "")
    encoded = str(value or "").encode("utf-8")
    if len(encoded) < 32:
        raise EditorAccessTicketError(
            "VECTOPLAN_EDITOR_ACCESS_TICKET_SECRET must contain at least 32 bytes."
        )
    return encoded


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def mint_editor_access_ticket(
    claims: Mapping[str, Any],
    *,
    ttl_seconds: int | None = None,
    now: int | None = None,
) -> str:
    issued_at = int(time.time() if now is None else now)
    configured_ttl = _config_value(
        "VECTOPLAN_EDITOR_ACCESS_TICKET_TTL_SECONDS",
        DEFAULT_TTL_SECONDS,
    )
    try:
        ttl = int(configured_ttl if ttl_seconds is None else ttl_seconds)
    except Exception:
        ttl = DEFAULT_TTL_SECONDS
    ttl = max(30, min(ttl, MAX_TTL_SECONDS))

    payload = dict(claims)
    payload.update(
        {
            "v": TICKET_VERSION,
            "iss": TICKET_ISSUER,
            "aud": TICKET_AUDIENCE,
            "iat": issued_at,
            "exp": issued_at + ttl,
            "jti": secrets.token_urlsafe(18),
        }
    )
    encoded_payload = _b64encode(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = _b64encode(
        hmac.new(_secret(), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{encoded_payload}.{signature}"


__all__ = ["EditorAccessTicketError", "mint_editor_access_ticket"]
