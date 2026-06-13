# /services/app/src/vectoplan/api.py
from __future__ import annotations

"""
VectoPlan API-Basics

Zentralisiert:
- cfg()                   → Host/Token aus Flask-Config
- headers(), auth_headers → HTTP-Header (JSON vs. Multipart)
- timeout_upload()        → Upload-Timeout
- gql(query, variables)   → GraphQL-Client
- REST-Helfer: rest_get / rest_post_json / rest_post_files / rest_delete

Alle Funktionen loggen robust über current_app.logger.
"""

from typing import Any, Dict, Optional, Tuple

import requests
from flask import current_app


# ───────────────────────── internals ─────────────────────────

def _log(level: str, msg: str, *args) -> None:
    try:
        logger = getattr(current_app, "logger", None)
        if logger and hasattr(logger, level):
            getattr(logger, level)(msg, *args)
    except Exception:
        pass


def _url(host: str, path: str) -> str:
    try:
        p = (path or "").lstrip("/")
        return f"{host}/{p}"
    except Exception:
        return host.rstrip("/") + "/" + (path or "").lstrip("/")


# ───────────────────────── public: config/headers ─────────────────────────

def cfg() -> Tuple[str, Optional[str]]:
    """
    Host + Token. Host ohne trailing slash.
    """
    try:
        host = (current_app.config.get("VECTOPLAN_HOST", "https://vectoplan.com") or "").rstrip("/")
        token = current_app.config.get("VECTOPLAN_TOKEN")
        return host, token
    except Exception:
        return "https://vectoplan.com", None


def headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def auth_headers(token: str) -> Dict[str, str]:
    # Für Multipart: Content-Type nicht setzen, requests bestimmt Boundary selbst.
    return {"Authorization": f"Bearer {token}"}


def timeout_upload() -> int:
    try:
        return int(current_app.config.get("SPECKLE_UPLOAD_TIMEOUT", 300))
    except Exception:
        return 300


# ───────────────────────── public: GraphQL ─────────────────────────

def gql(query: str, variables: Optional[Dict[str, Any]] = None, *, timeout_s: int = 15) -> Optional[Dict[str, Any]]:
    """
    GraphQL-Call. Rückgabe: data{} oder None.
    """
    host, token = cfg()
    if not token:
        _log("warning", "vectoplan.gql: missing token")
        return None
    try:
        r = requests.post(
            _url(host, "graphql"),
            json={"query": query, "variables": variables or {}},
            headers=headers(token),
            timeout=timeout_s,
        )
        if r.status_code >= 400:
            _log("error", "Vectoplan GraphQL HTTP %s: %s", r.status_code, (r.text or "")[:500])
            r.raise_for_status()
        data = r.json() or {}
        if data.get("errors"):
            _log("error", "Vectoplan GraphQL errors: %s", data["errors"])
            return None
        return data.get("data")
    except Exception as ex:
        _log("error", "Vectoplan GraphQL exception: %s", ex)
        return None


# ───────────────────────── public: REST helpers ─────────────────────────

def rest_get(path: str, *, params: Optional[Dict[str, Any]] = None, timeout_s: int = 15) -> Tuple[int, Optional[Dict[str, Any]]]:
    """
    GET /absolute_path
    Rückgabe: (status_code, json|None)
    """
    host, token = cfg()
    if not token:
        return 401, None
    try:
        r = requests.get(_url(host, path), params=params or {}, headers=headers(token), timeout=timeout_s)
        js = None
        try:
            js = r.json()
        except Exception:
            js = None
        return r.status_code, js
    except Exception as ex:
        _log("warning", "rest_get %s failed: %s", path, ex)
        return 599, None


def rest_post_json(path: str, *, payload: Dict[str, Any], timeout_s: int = 15) -> Tuple[int, Optional[Dict[str, Any]]]:
    """
    POST JSON → (status, json|None)
    """
    host, token = cfg()
    if not token:
        return 401, None
    try:
        r = requests.post(_url(host, path), json=payload or {}, headers=headers(token), timeout=timeout_s)
        js = None
        try:
            js = r.json()
        except Exception:
            js = None
        return r.status_code, js
    except Exception as ex:
        _log("warning", "rest_post_json %s failed: %s", path, ex)
        return 599, None


def rest_post_files(
    path: str,
    *,
    files: Dict[str, Tuple[str, bytes, str]],
    data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout_s: Optional[int] = None,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """
    POST Multipart → (status, json|None)
    files: {"file": (filename, bytes, mime)}
    """
    host, token = cfg()
    if not token:
        return 401, None
    try:
        r = requests.post(
            _url(host, path),
            params=params or {},
            files=files or {},
            data=data or {},
            headers=auth_headers(token),
            timeout=timeout_s or timeout_upload(),
        )
        js = None
        try:
            js = r.json()
        except Exception:
            js = None
        return r.status_code, js
    except Exception as ex:
        _log("warning", "rest_post_files %s failed: %s", path, ex)
        return 599, None


def rest_delete(path: str, *, params: Optional[Dict[str, Any]] = None, timeout_s: int = 15) -> Tuple[int, Optional[Dict[str, Any]]]:
    """
    DELETE /absolute_path
    Rückgabe: (status_code, json|None)
    Hinweis: Viele DELETE-Endpunkte antworten mit 204 (kein Body).
    """
    host, token = cfg()
    if not token:
        return 401, None
    try:
        r = requests.delete(_url(host, path), params=params or {}, headers=headers(token), timeout=timeout_s)
        js = None
        # 204/205 → kein JSON, andere evtl. JSON
        if r.status_code not in (204, 205):
            try:
                js = r.json()
            except Exception:
                js = None
        return r.status_code, js
    except Exception as ex:
        _log("warning", "rest_delete %s failed: %s", path, ex)
        return 599, None


__all__ = [
    "cfg",
    "headers",
    "auth_headers",
    "timeout_upload",
    "gql",
    "rest_get",
    "rest_post_json",
    "rest_post_files",
    "rest_delete",
]
