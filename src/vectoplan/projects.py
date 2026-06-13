# /services/app/src/vectoplan/projects.py
from __future__ import annotations

"""
Projekte & Modelle

- create_stream(name)                   → stream/project id
- fetch_models(project_id)              → [ {id,name,createdAt,updatedAt}, ... ]
- ensure_and_refresh(conv)              → stream anlegen, jüngstes Modell in conv cachen
- ensure_model(project_id, preferred)   → jüngstes Modell oder anlegen

Alle Funktionen sind robust, loggen über current_app.logger und tolerieren fehlerhafte Server.
"""

from typing import Any, Dict, List, Optional

from flask import current_app
from extensions import db
from models import Conversation

from .api import cfg, headers, gql, rest_get, rest_post_json


# ───────────────────────── internals ─────────────────────────

def _log(level: str, msg: str, *args) -> None:
    try:
        logger = getattr(current_app, "logger", None)
        if logger and hasattr(logger, level):
            getattr(logger, level)(msg, *args)
    except Exception:
        pass


def _dt(s: Optional[str]):
    try:
        return __import__("datetime").datetime.fromisoformat((s or "").rstrip("Z"))
    except Exception:
        return __import__("datetime").datetime.fromtimestamp(0)


# ───────────────────────── Streams/Projekte ─────────────────────────

def create_stream(name: str) -> Optional[str]:
    """
    Project/Stream anlegen (REST, Fallback GraphQL).
    """
    host, token = cfg()
    if not token:
        return None

    # REST
    try:
        status, js = rest_post_json("api/streams", payload={"name": name, "description": ""})
        if status in (200, 201, 202):
            if isinstance(js, dict):
                return js.get("id")
            return None
    except Exception as ex:
        _log("warning", "create_stream REST failed: %s", ex)

    # GraphQL
    try:
        q = "mutation ($s: StreamCreateInput!){ streamCreate(stream:$s) }"
        d = gql(q, {"s": {"name": name, "description": ""}})
        return d["streamCreate"] if d else None
    except Exception:
        return None


def fetch_models(project_id: str) -> List[Dict[str, Any]]:
    """
    Modelle eines Projekts. Versucht GraphQL, fällt auf REST zurück.
    """
    host, token = cfg()
    if not token:
        return []
    # GraphQL
    try:
        q = """query ($pid:String!){
          project(id:$pid){ models{ items{ id name createdAt updatedAt } } }
        }"""
        d = gql(q, {"pid": project_id})
        if d:
            items = d.get("project", {}).get("models", {}).get("items") or []
            return [dict(x) for x in items if isinstance(x, dict)]
    except Exception:
        pass
    # REST
    try:
        status, js = rest_get(f"api/projects/{project_id}/models")
        if status in (200, 201) and isinstance(js, dict):
            return [dict(x) for x in (js.get("items") or []) if isinstance(x, dict)]
    except Exception as ex:
        _log("error", "fetch_models REST error: %s", ex)
    return []


def ensure_and_refresh(conv: Conversation) -> None:
    """
    • legt Projekt-Stream an, falls fehlt
    • cached jüngstes Modell (falls vorhanden) in conv.vectoplan_model_id
    """
    try:
        _, token = cfg()
        if not token:
            return
        changed = False

        if not getattr(conv, "vectoplan_project_id", None):
            try:
                sid = create_stream(conv.title or f"Chat {conv.id}")
                if sid:
                    conv.vectoplan_project_id = sid
                    changed = True
                    _log("info", "Vectoplan stream created: chat %s -> %s", conv.id, sid)
            except Exception as ex:
                _log("warning", "create_stream failed: %s", ex)

        pid = getattr(conv, "vectoplan_project_id", None)
        if pid:
            try:
                items = fetch_models(pid) or []
                if items:
                    latest = max(items, key=lambda m: (_dt(m.get("updatedAt")),
                                                       _dt(m.get("createdAt")),
                                                       m.get("id", "")))
                    if latest.get("id") and latest["id"] != getattr(conv, "vectoplan_model_id", None):
                        conv.vectoplan_model_id = latest["id"]
                        changed = True
            except Exception as ex:
                _log("warning", "fetch_models failed: %s", ex)

        if changed:
            try:
                db.session.add(conv)
                db.session.commit()
            except Exception as ex:
                _log("warning", "DB commit failed: %s", ex)
    except Exception:
        pass


def ensure_model(project_id: str, preferred_name: Optional[str] = None) -> Optional[str]:
    """
    Liefert eine gültige model_id. Nimmt das jüngste Modell oder legt eines an.
    """
    try:
        items = fetch_models(project_id) or []
        if items:
            latest = max(items, key=lambda m: (_dt(m.get("updatedAt")),
                                               _dt(m.get("createdAt")),
                                               m.get("id", "")))
            return latest.get("id")
        # Keins vorhanden → anlegen
        name = (preferred_name or "model").strip() or "model"
        # GraphQL Create nicht verfügbar → REST:
        host, token = cfg()
        if not token:
            return None
        status, js = rest_post_json(f"api/projects/{project_id}/models", payload={"name": name})
        if status in (200, 201) and isinstance(js, dict):
            return js.get("id") or js.get("modelId")
        return None
    except Exception as ex:
        _log("warning", "ensure_model failed: %s", ex)
        return None
