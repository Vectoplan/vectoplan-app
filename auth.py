# /services/app/auth.py
from __future__ import annotations

import logging
from functools import wraps
from hmac import compare_digest
from typing import Optional, Tuple

from flask import request, jsonify

from extensions import db
from models import Client, IdempotencyKey

log = logging.getLogger(__name__)


def _err(msg: str, code: int):
    return jsonify({"error": msg}), code


def _extract_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Header-Varianten:
      X-Client-Id: <id>
      X-Api-Key:   <key>
    Oder:
      Authorization: ApiKey <id>:<key>
    """
    cid = request.headers.get("X-Client-Id")
    key = request.headers.get("X-Api-Key")

    if not cid or not key:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("ApiKey "):
            try:
                token = auth[len("ApiKey "):].strip()
                parts = token.split(":", 1)
                if len(parts) == 2:
                    cid, key = parts[0], parts[1]
            except Exception:  # schluckt Parsingfehler
                pass
    return cid, key


def _load_client(cid: str) -> Optional[Client]:
    try:
        return Client.query.get(cid)
    except Exception as ex:
        log.exception("DB-Fehler bei Client-Lookup: %s", ex)
        return None


def _check_idempotency(client_id: str) -> Optional[Tuple[dict, int]]:
    idem = request.headers.get("Idempotency-Key")
    if not idem:
        return None
    try:
        existing = IdempotencyKey.query.filter_by(client_id=client_id, key=idem).first()
        if existing:
            if existing.is_valid():
                # bereits benutzt innerhalb TTL
                return _err("duplicate idempotency key", 409)
            # abgelaufen -> neu setzen
            db.session.delete(existing)
            db.session.commit()
        rec = IdempotencyKey(client_id=client_id, key=idem)
        db.session.add(rec)
        db.session.commit()
        return None
    except Exception as ex:
        log.exception("Idempotency-Fehler: %s", ex)
        return _err("idempotency check failed", 500)


def require_api_key(scope: Optional[str] = None):
    """
    Dekorator für geschützte Routen.
    - prüft Header-Creds
    - prüft Aktivität und Scopes
    - optional Idempotency-Key
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            cid, key = _extract_credentials()
            if not cid or not key:
                return _err("missing credentials", 401)

            client = _load_client(cid)
            if not client:
                return _err("unknown client", 401)
            if not client.is_active:
                return _err("client disabled", 403)

            try:
                ok = compare_digest(client.api_key_hash, Client.hash_key(key))
            except Exception:
                ok = False
            if not ok:
                return _err("invalid key", 401)

            if scope and not client.has_scope(scope):
                return _err("forbidden: missing scope", 403)

            idem_res = _check_idempotency(client.id)
            if idem_res:
                return idem_res  # 409 oder 500

            # optional: Request-Context mit client_id
            request.client = client  # bewusst einfach

            try:
                return fn(*args, **kwargs)
            except Exception as ex:
                log.exception("Unhandled error in route: %s", ex)
                return _err("internal error", 500)
        return wrapper
    return deco
