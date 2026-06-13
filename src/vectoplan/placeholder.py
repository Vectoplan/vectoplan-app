# /services/app/src/vectoplan/placeholder.py
from __future__ import annotations

"""
Platzhalter-Anlage (einheitlich mit EMBED)

ensure_placeholder_if_empty(conv):
  - Wenn im Projekt noch KEIN Modell existiert:
      • Modell anlegen
      • Platzhalter-Commit (Point) schreiben
      • conv.vectoplan_model_id speichern
      • Model-Viewer-URL (mit #embed) zurückgeben
  - Fallback (wenn Model-Create scheitert):
      • Commit direkt auf dem Stream
      • Commit-Viewer-URL (mit #embed) zurückgeben
  - Existiert bereits ein Modell → None (Aufrufer nutzt viewer_url(conv))

Alle zurückgegebenen URLs sind im **Embed-Modus** und damit visuell konsistent:
- Keine Top-Navigation
- Linke Toolbar sichtbar (hideControls=False)
- Rechte Infoleiste ausgeblendet (hideSelectionInfo=True)
"""

from typing import Any, Dict, Optional

from flask import current_app
from extensions import db
from models import Conversation

from .api import cfg, gql, rest_post_json
from .projects import fetch_models, ensure_model, create_stream
from .viewer import viewer_url, viewer_url_for, commit_viewer_url


# ───────────────────────── internals ─────────────────────────

def _log(level: str, msg: str, *args) -> None:
    try:
        logger = getattr(current_app, "logger", None)
        if logger and hasattr(logger, level):
            getattr(logger, level)(msg, *args)
    except Exception:
        pass


def _q(s: str) -> str:
    try:
        import requests
        return requests.utils.quote(s, safe="")
    except Exception:
        return s


def _point() -> Dict[str, Any]:
    # sehr kleines, serververträgliches Speckle-Objekt
    return {"speckle_type": "Objects.Geometry.Point", "x": 0.0, "y": 0.0, "z": 0.0, "units": "m"}


# ---------- Objekterzeugung ----------

def _object_create_graphql(stream_id: str, obj: dict) -> Optional[str]:
    """
    GraphQL: objectCreate(objectInput:{streamId, objects:[...]})
    Rückgabe: objectId
    """
    try:
        q = "mutation ($input:ObjectCreateInput!){ objectCreate(objectInput:$input) }"
        d = gql(q, {"input": {"streamId": stream_id, "objects": [obj]}})
        if not d:
            return None
        val = d.get("objectCreate")
        if isinstance(val, list) and val:
            return str(val[0])
        if isinstance(val, str):
            return val
        return None
    except Exception:
        return None


def _object_create_rest_stream(stream_id: str, obj: dict) -> Optional[str]:
    try:
        status, js = rest_post_json(f"api/streams/{_q(stream_id)}/objects", payload={"objects": [obj]})
        if status in (200, 201) and isinstance(js, dict):
            objs = js.get("objects") or []
            if objs and isinstance(objs[0], dict):
                return objs[0].get("id")
    except Exception as ex:
        _log("warning", "objects REST stream failed: %s", ex)
    return None


def _object_create_rest_generic(stream_id: str, obj: dict) -> Optional[str]:
    try:
        # /api/objects?streamId=...
        status, js = rest_post_json(f"api/objects?streamId={_q(stream_id)}", payload={"objects": [obj]})
        if status in (200, 201) and isinstance(js, dict):
            objs = js.get("objects") or []
            if objs and isinstance(objs[0], dict):
                return objs[0].get("id")
    except Exception as ex:
        _log("warning", "objects REST generic failed: %s", ex)
    return None


def _create_object_any(stream_or_model_id: str, obj: dict) -> Optional[str]:
    return (_object_create_graphql(stream_or_model_id, obj)
            or _object_create_rest_stream(stream_or_model_id, obj)
            or _object_create_rest_generic(stream_or_model_id, obj))


# ---------- Commit ----------

def _commit_create_graphql(stream_or_model_id: str, object_id: str) -> Optional[str]:
    """
    commitCreate – neue wie ältere Signatur probieren.
    """
    payload = {
        "streamId": stream_or_model_id,
        "objectId": object_id,
        "branchName": "main",
        "message": "placeholder",
        "sourceApplication": "vectoplan-bootstrap",
    }
    # commit: …
    try:
        q1 = "mutation ($commit: CommitCreateInput!) { commitCreate(commit: $commit) }"
        d1 = gql(q1, {"commit": payload})
        if d1 and d1.get("commitCreate"):
            v = d1["commitCreate"]
            return str(v[0] if isinstance(v, list) and v else v)
    except Exception:
        pass
    # input: …
    try:
        q2 = "mutation ($input: CommitCreateInput!) { commitCreate(input: $input) }"
        d2 = gql(q2, {"input": payload})
        if d2 and d2.get("commitCreate"):
            v = d2["commitCreate"]
            return str(v[0] if isinstance(v, list) and v else v)
    except Exception:
        pass
    return None


def _commit_create_rest(stream_or_model_id: str, object_id: str) -> Optional[str]:
    try:
        # POST /api/streams/{id}/commits
        status, js = rest_post_json(f"api/streams/{_q(stream_or_model_id)}/commits", payload={
            "streamId": stream_or_model_id,
            "objectId": object_id,
            "branchName": "main",
            "message": "placeholder",
            "sourceApplication": "vectoplan-bootstrap",
        })
        if status in (200, 201) and isinstance(js, dict):
            return js.get("id") or js.get("commitId") or js.get("versionId")
    except Exception as ex:
        _log("warning", "commit REST failed: %s", ex)
    return None


def _commit_placeholder(stream_or_model_id: str) -> Optional[str]:
    """
    Erzeugt einen Punkt und committet ihn. Rückgabe: commitId (optional).
    """
    try:
        oid = _create_object_any(stream_or_model_id, _point())
        if not oid:
            return None
        return _commit_create_graphql(stream_or_model_id, oid) or _commit_create_rest(stream_or_model_id, oid)
    except Exception:
        return None


# ───────────────────────── public: ensure_placeholder_if_empty ─────────────────────────

def ensure_placeholder_if_empty(conv: Conversation) -> Optional[str]:
    """
    Wenn im Projekt noch KEIN Modell existiert:
      • Modell anlegen + Platzhalter-Commit → Model-Viewer-URL (#embed)
      • Fallback: Stream-Commit → Commit-Viewer-URL (#embed)
      • conv.vectoplan_model_id wird gesetzt, falls ein Modell erzeugt wurde.
    Wenn schon Modelle existieren → None.
    """
    try:
        host, token = cfg()
        if not token:
            return None

        # Projekt/Stream sicherstellen
        pid = getattr(conv, "vectoplan_project_id", None)
        if not pid:
            sid = create_stream(conv.title or f"Chat {conv.id}")
            if not sid:
                return None
            conv.vectoplan_project_id = sid
            try:
                db.session.add(conv); db.session.commit()
            except Exception:
                pass
            pid = sid

        # Modelle vorhanden?
        items = fetch_models(pid) or []
        if items:
            return None  # nichts zu tun

        # 1) Modell anlegen
        name = (getattr(conv, "title", None) or f"chat-{conv.id}").strip() or "model"
        mid = ensure_model(pid, preferred_name=name) or ensure_model(pid, preferred_name="model")
        if mid:
            # Platzhalter-Commit auf dem Modell (Speckle akzeptiert modelId als streamId)
            try:
                _ = _commit_placeholder(mid)
            except Exception:
                _log("warning", "placeholder: commit on model failed", exc_info=True)
            # Conversation aktualisieren
            try:
                conv.vectoplan_model_id = mid
                db.session.add(conv); db.session.commit()
            except Exception:
                pass
            # Einheitliche Model-Viewer URL (mit Embed-Fragment)
            try:
                return viewer_url_for(pid, mid) or viewer_url(conv)
            except Exception:
                return viewer_url(conv)

        # 2) Fallback: Stream-Commit (liefert Commit-Viewer-URL mit Embed)
        try:
            cid = _commit_placeholder(pid)
            if cid:
                return commit_viewer_url(pid, cid)
        except Exception:
            _log("warning", "placeholder: stream commit failed", exc_info=True)

        _log("warning", "placeholder: cannot create model for project %s", pid)
        return None

    except Exception as ex:
        _log("error", "ensure_placeholder_if_empty error: %s", ex)
        return None
