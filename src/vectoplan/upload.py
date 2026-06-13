# /services/app/src/vectoplan/upload.py
from __future__ import annotations

"""
Upload-Flow (robust + konsistente Commit-Message)

upload_file_to_project(conv, blob, model_name=None, file_ext=None) -> Dict[str, Any]

- Stellt Projekt (Stream) sicher
- Stellt Modell sicher (nimmt jüngstes oder legt eines an)
- Hängt Datei als neue Version an; Fallbacks:
    (a) POST /api/projects/{pid}/models/{mid}/versions
    (b) POST /api/projects/{pid}/models  (mit Datei → neues Modell + Version)
    (c) POST /api/uploads?projectId=...  oder  /api/projects/{pid}/uploads
- Polling bis der Server die Version verarbeitet hat
- Ermittelt anschließend die **tatsächlich neueste** Version-ID des Modells
- Gibt {project_id, model_id, version_id, viewer_url, commit_message} zurück

Wichtig:
- Die Commit-Message wird auf einen **sauberen Dateinamen + Zeitstempel** gesetzt,
  damit Speckle-Liste und unsere lokale Versionsliste übereinstimmen können.
  (z. B. „Klostermeier-V2.ifc__13.09.2025__09.08“)
"""

from typing import Any, Dict, Optional, Tuple, List

from flask import current_app
from extensions import db
from models import Conversation, Blob

from .api import cfg, rest_post_files, rest_post_json, rest_get
from .projects import ensure_model, create_stream, fetch_models
from .viewer import viewer_url


# ───────────────────────── internals ─────────────────────────

def _log(level: str, msg: str, *args) -> None:
    """Zentralisierte, robuste Logger-Hülle."""
    try:
        logger = getattr(current_app, "logger", None)
        if logger and hasattr(logger, level):
            getattr(logger, level)(msg, *args)
    except Exception:
        pass


def _now_label() -> str:
    try:
        from datetime import datetime
        return datetime.now().strftime("%d.%m.%Y__%H.%M")
    except Exception:
        return "00.00.0000__00.00"


def _safe_label(base: str) -> str:
    """Erzeugt einen sicheren Label-Teil (Dateiname → alnum + -_.), und kürzt sanft."""
    try:
        b = (base or "").strip()
        try:
            import os
            b = os.path.basename(b)
        except Exception:
            pass
        if not b:
            b = "upload"
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in b)
        return safe[:120] or "upload"
    except Exception:
        return "upload"


def _make_commit_message(blob: Blob, fallback: str = "upload") -> str:
    """
    Konsistente Commit-Message wie unsere lokale Versionsbenennung:
      <safe(filename)>__<DD.MM.YYYY__HH.MM>
    """
    try:
        base = _safe_label(blob.filename or fallback)
        msg = f"{base}__{_now_label()}"
        return msg[:180]  # defensive cap
    except Exception:
        return f"{fallback}__{_now_label()}"[:180]


def _rest_create_model_with_file(pid: str, name: str, blob: Blob, commit_message: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Neues Modell anlegen und Datei mitsenden, falls der Server das unterstützt.
    Rückgabe: (model_id, version_id|upload_id)
    """
    try:
        status, js = rest_post_files(
            f"api/projects/{pid}/models",
            files={"file": (blob.filename or "file", blob.data, blob.mime or "application/octet-stream")},
            data={
                "name": name,
                "message": commit_message,
                "sourceApplication": "vectoplan-app",
            },
        )
        _log("info", "create_model_with_file: status=%s body_keys=%s", status, list((js or {}).keys()))
        if status in (200, 201, 202) and isinstance(js, dict):
            mid = js.get("id") or js.get("modelId")
            vid = js.get("versionId") or js.get("commitId") or js.get("uploadId")
            return mid, vid
    except Exception as ex:
        _log("info", "create_model_with_file failed: %s", ex)
    return None, None


def _rest_upload_version(pid: str, mid: str, blob: Blob, commit_message: str) -> Optional[str]:
    """
    Neue Version an existierendes Modell hängen.
    Rückgabe: version_id|upload_id oder None.
    """
    try:
        status, js = rest_post_files(
            f"api/projects/{pid}/models/{mid}/versions",
            files={"file": (blob.filename or "file", blob.data, blob.mime or "application/octet-stream")},
            data={
                "message": commit_message,
                "sourceApplication": "vectoplan-app",
            },
        )
        _log("info", "upload_version: status=%s body_keys=%s", status, list((js or {}).keys()))
        if status in (200, 201, 202) and isinstance(js, dict):
            return js.get("id") or js.get("versionId") or js.get("commitId") or js.get("uploadId")
    except Exception as ex:
        _log("info", "upload_version failed: %s", ex)
    return None


def _rest_generic_upload(pid: str, blob: Blob, commit_message: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Generische Upload-Fallbacks auf Projektebene.
    Rückgabe: (model_id, version_id|upload_id)
    """
    # A) /api/uploads?projectId=...
    try:
        status, js = rest_post_files(
            "api/uploads",
            files={"file": (blob.filename or "file", blob.data, blob.mime or "application/octet-stream")},
            params={"projectId": pid},
            data={"message": commit_message, "sourceApplication": "vectoplan-app"},
        )
        _log("info", "generic uploads: status=%s body_keys=%s", status, list((js or {}).keys()))
        if status in (200, 201, 202) and isinstance(js, dict):
            mid = js.get("modelId")
            vid = js.get("versionId") or js.get("uploadId")
            if mid or vid:
                return mid, vid
    except Exception as ex:
        _log("info", "generic upload (uploads) failed: %s", ex)

    # B) /api/projects/{pid}/uploads
    try:
        status2, js2 = rest_post_files(
            f"api/projects/{pid}/uploads",
            files={"file": (blob.filename or "file", blob.data, blob.mime or "application/octet-stream")},
            data={"message": commit_message, "sourceApplication": "vectoplan-app"},
        )
        _log("info", "generic project/uploads: status=%s body_keys=%s", status2, list((js2 or {}).keys()))
        if status2 in (200, 201, 202) and isinstance(js2, dict):
            mid = js2.get("modelId")
            vid = js2.get("versionId") or js2.get("uploadId")
            if mid or vid:
                return mid, vid
    except Exception as ex:
        _log("info", "generic upload (project/uploads) failed: %s", ex)

    return None, None


def _poll_model_ready(pid: str, mid: str, max_wait_s: int = 90) -> None:
    """
    Best-effort Polling, bis das Modell eine aktualisierte updatedAt hat.
    Fehler werden nicht eskaliert.
    """
    try:
        import time
        from datetime import datetime
    except Exception:
        return

    deadline = datetime.utcnow().timestamp() + max_wait_s

    def _probe_updated_at() -> Optional[str]:
        try:
            items = fetch_models(pid) or []
            for m in items:
                if m.get("id") == mid:
                    return m.get("updatedAt") or m.get("createdAt")
        except Exception:
            return None
        return None

    last = _probe_updated_at()
    _log("info", "poll: start last_updated=%s", last)
    while time.time() < deadline:
        try:
            time.sleep(2)
            now = _probe_updated_at()
            if now and now != last:
                _log("info", "poll: model updatedAt changed -> %s", now)
                return
        except Exception:
            return
    _log("info", "poll: timeout reached (no updatedAt change)")


def _list_model_versions(pid: str, mid: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Liefert die neuesten Modell-Versionen (REST). Struktur:
      {"items":[{"id"| "versionId"| "commitId": "...", "message": "...", ...}], ...}
    """
    try:
        status, js = rest_get(f"api/projects/{pid}/models/{mid}/versions", params={"limit": limit})
        _log("info", "list_versions: status=%s keys=%s", status, list((js or {}).keys()))
        if status in (200, 201) and isinstance(js, dict):
            items = js.get("items") or js.get("versions") or []
            return [x for x in items if isinstance(x, dict)]
    except Exception as ex:
        _log("warning", "list_model_versions failed: %s", ex)
    return []


def _pick_id(ver: Dict[str, Any]) -> Optional[str]:
    """Extrahiert eine brauchbare Version-/Commit-ID."""
    try:
        return ver.get("id") or ver.get("versionId") or ver.get("commitId") or None
    except Exception:
        return None


def _verify_alignment(pid: str, mid: str, final_vid: Optional[str], provisional: Optional[str], commit_message: str) -> None:
    """
    Debug/Tracing: prüft grob, ob finale ID im Server-Listing auftaucht und loggt Message.
    """
    try:
        vers = _list_model_versions(pid, mid, limit=10)
        server_ids = [(_pick_id(v) or "") for v in vers]
        server_msgs = [str(v.get("message") or "") for v in vers]
        _log(
            "info",
            "verify: server_top_ids=%s messages=%s final=%s provisional=%s wanted_message=%s",
            server_ids, server_msgs, final_vid, provisional, commit_message
        )
        if final_vid and final_vid not in server_ids:
            _log("warning", "verify: final version_id not in server top list")
    except Exception as ex:
        _log("warning", "verify_alignment failed: %s", ex)


# ───────────────────────── public: Upload ─────────────────────────

def upload_file_to_project(
    conv: Conversation,
    blob: Blob,
    model_name: Optional[str] = None,
    file_ext: Optional[str] = None,
) -> Dict[str, Any]:
    """
    High-level Upload-Fassade.
    • Stellt Projekt sicher
    • Wählt/erstellt ein Modell
    • Hängt Datei als neue Version an oder erzeugt Modell aus Datei
    • Pollt bis verarbeitet und ermittelt anschließend die **tatsächliche** neueste Version-ID
    • Liefert {project_id, model_id, version_id, viewer_url, commit_message}
    """
    host, token = cfg()
    if not token:
        raise RuntimeError("missing VECTOPLAN_TOKEN")

    # 0) Commit-Message vorab bestimmen (soll lokalem Label entsprechen)
    commit_message = _make_commit_message(blob, fallback="upload")

    # 1) Projekt sicherstellen
    try:
        if not getattr(conv, "vectoplan_project_id", None):
            sid = create_stream(conv.title or f"Chat {conv.id}")
            if not sid:
                raise RuntimeError("cannot create project/stream")
            conv.vectoplan_project_id = sid
            db.session.add(conv)
            db.session.commit()
            _log("info", "project created: %s", sid)
    except Exception as ex:
        _log("warning", "upload: ensure project failed: %s", ex)
        raise

    pid = conv.vectoplan_project_id

    # 2) Modell sicherstellen
    try:
        mid = getattr(conv, "vectoplan_model_id", None)
        if not mid:
            mid = ensure_model(pid, preferred_name=(model_name or (blob.filename or "model")))
            if mid:
                conv.vectoplan_model_id = mid
                db.session.add(conv)
                db.session.commit()
                _log("info", "model ensured: %s", mid)
    except Exception as ex:
        _log("warning", "upload: ensure model failed: %s", ex)
        mid = getattr(conv, "vectoplan_model_id", None)

    # 3) Upload versuchen
    provisional_version_id: Optional[str] = None

    # 3a) Version an existierendes Modell hängen
    if mid:
        provisional_version_id = _rest_upload_version(pid, mid, blob, commit_message)

    # 3b) Modell + Datei auf einmal anlegen
    if not provisional_version_id:
        nm = (model_name or (blob.filename or "upload")).rsplit(".", 1)[0][:80] or "model"
        new_mid, vid = _rest_create_model_with_file(pid, nm, blob, commit_message)
        if new_mid:
            mid = new_mid
            try:
                conv.vectoplan_model_id = mid
                db.session.add(conv)
                db.session.commit()
            except Exception as ex:
                _log("warning", "upload: DB commit after create_model_with_file failed: %s", ex)
        provisional_version_id = provisional_version_id or vid

    # 3c) Generischer Upload-Fallback
    if not provisional_version_id:
        new_mid2, vid2 = _rest_generic_upload(pid, blob, commit_message)
        if new_mid2 and not mid:
            mid = new_mid2
            try:
                conv.vectoplan_model_id = mid
                db.session.add(conv)
                db.session.commit()
            except Exception as ex:
                _log("warning", "upload: DB commit after generic upload set model failed: %s", ex)
        provisional_version_id = provisional_version_id or vid2

    # 3d) Letzter Versuch: Modell ohne Datei nur erzeugen, damit Viewer funktioniert
    if not mid:
        mid = ensure_model(pid, preferred_name=(model_name or "model"))
        if mid:
            try:
                conv.vectoplan_model_id = mid
                db.session.add(conv)
                db.session.commit()
            except Exception:
                pass

    if not (provisional_version_id or mid):
        raise RuntimeError("upload failed")

    _log("info", "upload provisional_id=%s model_id=%s commit_message=%s", provisional_version_id, mid, commit_message)

    # 4) Verarbeitung abwarten
    try:
        if mid:
            _poll_model_ready(pid, mid, max_wait_s=60)
    except Exception:
        pass

    # 5) Tatsächliche neueste Version vom Server holen
    final_version_id = None
    try:
        vers = _list_model_versions(pid, mid, limit=10)
        if vers:
            final_version_id = _pick_id(vers[0])
            _log("info", "server latest version_id=%s (top-of-list)", final_version_id)
    except Exception:
        final_version_id = None

    version_id = final_version_id or provisional_version_id

    # 6) Sanity-Check/Tracing
    _verify_alignment(pid, mid, version_id, provisional_version_id, commit_message)

    # 7) Viewer-URL (Model-Viewer)
    try:
        vurl = viewer_url(conv) or ""
    except Exception:
        vurl = ""

    # Abschluss-Log
    _log(
        "info",
        "upload done: project=%s model=%s version=%s (provisional=%s) viewer_url=%s commit_message=%s",
        pid, mid, version_id, provisional_version_id, vurl, commit_message
    )

    return {
        "project_id": pid,
        "model_id": mid,
        "version_id": version_id,            # möglichst echte Commit-ID
        "viewer_url": vurl,
        "commit_message": commit_message,    # für Aufrufer, um lokales Label identisch zu setzen
    }
