# /services/app/routes/vectoplan_align.py
from __future__ import annotations
"""
Align/Synchronize Speckle (Vectoplan) with local version history.

Endpoints
- POST /v1/chats/<chat_id>/vectoplan/align
    Wählt im Viewer (viewer_selection) die *neueste lokale* Version mit gültigen
    Speckle-IDs. Fallback: neueste Server-Version des aktuellen Modells.

- POST /v1/chats/<chat_id>/vectoplan/purge-and-reupload
    Löscht auf dem Speckle-Server alle Modell-Versionen, die NICHT in unserer
    lokalen Versionshistorie vorhanden sind, und lädt fehlende lokale Versionen
    (soweit möglich) erneut hoch (nur für .ifc/.obj/.stl mit vorhandenem Blob).
    Body JSON (optional): {"limit_reupload": 1} → wie viele lokale Top-Versionen
    sollen ggf. neu hochgeladen werden (default: 1).
"""

from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, current_app, request
from extensions import db
from models import Conversation, ConversationState, Blob
from vectoplan import ensure_and_refresh, viewer_url as vp_viewer_url, upload_file_to_project

bp = Blueprint("vectoplan_align", __name__)

# ───────────────────────── Mini-HTTP-Client (lokal, keine Paket-Abhängigkeit) ─────────────────────────

def _cfg() -> Tuple[str, Optional[str]]:
    host = (current_app.config.get("VECTOPLAN_HOST", "https://vectoplan.com") or "").rstrip("/")
    token = current_app.config.get("VECTOPLAN_TOKEN")
    return host, token

def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

def _url(host: str, path: str) -> str:
    p = (path or "").lstrip("/")
    return f"{host}/{p}"

def _rest_get(path: str, *, params: Optional[Dict[str, Any]] = None, timeout_s: int = 15) -> Tuple[int, Optional[Dict[str, Any]]]:
    import requests
    host, token = _cfg()
    if not token:
        return 401, None
    try:
        r = requests.get(_url(host, path), params=params or {}, headers=_headers(token), timeout=timeout_s)
        js = None
        try:
            js = r.json()
        except Exception:
            js = None
        return r.status_code, js
    except Exception as ex:
        try: current_app.logger.warning("align.rest_get %s failed: %s", path, ex)
        except Exception: pass
        return 599, None

def _rest_delete(path: str, *, params: Optional[Dict[str, Any]] = None, timeout_s: int = 15) -> Tuple[int, Optional[Dict[str, Any]]]:
    import requests
    host, token = _cfg()
    if not token:
        return 401, None
    try:
        r = requests.delete(_url(host, path), params=params or {}, headers=_headers(token), timeout=timeout_s)
        # 204/205 → kein JSON
        if r.status_code in (204, 205):
            return r.status_code, None
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, None
    except Exception as ex:
        try: current_app.logger.warning("align.rest_delete %s failed: %s", path, ex)
        except Exception: pass
        return 599, None

# ───────────────────────── Helpers ─────────────────────────

ALLOWED_REUPLOAD_EXTS = {".ifc", ".obj", ".stl"}

def _log(level: str, msg: str, *args) -> None:
    try:
        logger = getattr(current_app, "logger", None)
        if logger and hasattr(logger, level):
            getattr(logger, level)(msg, *args)
    except Exception:
        pass

def _pick_id(v: Dict[str, Any]) -> Optional[str]:
    try:
        return v.get("id") or v.get("versionId") or v.get("commitId") or None
    except Exception:
        return None

def _speckle_versions(pid: str, mid: str, limit: int = 20) -> List[Dict[str, Any]]:
    try:
        status, js = _rest_get(f"api/projects/{pid}/models/{mid}/versions", params={"limit": limit})
        if status in (200, 201) and isinstance(js, dict):
            items = js.get("items") or js.get("versions") or []
            return [x for x in items if isinstance(x, dict)]
    except Exception as ex:
        _log("warning", "align: list versions failed: %s", ex)
    return []

def _first_local_with_speckle(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    best = None
    for it in items or []:
        if not isinstance(it, dict):
            continue
        sp = (it.get("speckle") or it.get("meta", {}).get("speckle") or {}) if isinstance(it, dict) else {}
        if sp.get("project_id") and sp.get("model_id") and sp.get("version_id"):
            if not best:
                best = it
                continue
            try:
                if (it.get("version_idx", -1), str(it.get("created_at") or "")) > (best.get("version_idx", -1), str(best.get("created_at") or "")):
                    best = it
            except Exception:
                pass
    return best

def _ext_of(fn: str) -> str:
    try:
        import os
        return os.path.splitext(fn or "")[1].lower()
    except Exception:
        return ""

def _collect_local_versions(conv_id: str) -> List[Dict[str, Any]]:
    try:
        from versioning import list_versions_by_conversation
        return list_versions_by_conversation(conversation_id=conv_id, kind=None) or []
    except Exception:
        return []

def _local_commit_ids(local_items: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for it in local_items:
        try:
            sp = (it.get("speckle") or it.get("meta", {}).get("speckle") or {}) if isinstance(it, dict) else {}
            vid = sp.get("version_id") or sp.get("commit_id")
            if vid:
                out.append(str(vid))
        except Exception:
            continue
    return out

def _delete_server_versions_not_in(pid: str, mid: str, keep_ids: List[str]) -> Dict[str, Any]:
    report = {"checked": 0, "deleted": 0, "errors": 0, "kept": 0}
    try:
        keep = set(keep_ids or [])
        server = _speckle_versions(pid, mid, limit=50)
        report["checked"] = len(server)
        for v in server:
            vid = _pick_id(v) or ""
            if not vid:
                continue
            if vid in keep:
                report["kept"] = report["kept"] + 1
                continue
            # Delete
            st, _ = _rest_delete(f"api/projects/{pid}/models/{mid}/versions/{vid}")
            if st in (200, 202, 204, 205):
                report["deleted"] = report["deleted"] + 1
            else:
                report["errors"] = report["errors"] + 1
                _log("warning", "align: delete %s failed HTTP %s", vid, st)
    except Exception as ex:
        _log("warning", "align: purge server versions failed: %s", ex)
    return report

def _reupload_missing_local(conv: Conversation, pid: str, mid: str, *, limit_reupload: int = 1) -> Dict[str, Any]:
    """
    Lädt die neuesten *lokalen* Versionen (max. limit_reupload) erneut nach Speckle hoch,
    falls deren version_id NICHT auf dem Server vorhanden ist. Voraussetzung: input_blob
    existiert und ist .ifc/.obj/.stl.
    """
    info = {"considered": 0, "reuploaded": 0, "skipped_no_blob": 0, "skipped_ext": 0, "skipped_exists": 0, "errors": 0}
    try:
        local = _collect_local_versions(conv.id)
        # Nach version_idx (absteigend) sortieren als Daumenregel
        try:
            local.sort(key=lambda x: (x.get("version_idx", -1), str(x.get("created_at") or "")), reverse=True)
        except Exception:
            pass

        # Server-IDs-Menge
        server_ids = {_pick_id(v) for v in _speckle_versions(pid, mid, limit=50)}
        n = 0
        for it in local:
            if limit_reupload and n >= limit_reupload:
                break
            try:
                meta = it.get("meta") or {}
                sp = (it.get("speckle") or meta.get("speckle") or {}) if isinstance(it, dict) else {}
                blob_id = it.get("input_blob_id") or None
                kind = (it.get("kind") or "").upper()
                # bereits vorhanden?
                already = sp.get("version_id") or sp.get("commit_id")
                if already and already in server_ids:
                    info["skipped_exists"] += 1
                    continue
                if not blob_id:
                    info["skipped_no_blob"] += 1
                    continue
                b = Blob.query.get(str(blob_id))
                if not b:
                    info["skipped_no_blob"] += 1
                    continue
                ext = _ext_of(b.filename)
                if ext not in ALLOWED_REUPLOAD_EXTS:
                    info["skipped_ext"] += 1
                    continue

                info["considered"] += 1
                # Upload (benutzt robuste Fassade mit Commit-Message)
                up = upload_file_to_project(conv=conv, blob=b, model_name=None, file_ext=ext) or {}
                # Server-Liste aktualisieren
                server_ids.add(up.get("version_id"))
                info["reuploaded"] += 1
                n += 1
            except Exception as ex_item:
                _log("warning", "align: reupload failed: %s", ex_item, exc_info=True)
                info["errors"] += 1
    except Exception as ex:
        _log("warning", "align: reupload pass failed: %s", ex, exc_info=True)
    # persist conv changes if any
    try:
        db.session.add(conv); db.session.commit()
    except Exception:
        db.session.rollback()
    return info

# ───────────────────────── Routes ─────────────────────────

@bp.post("/v1/chats/<chat_id>/vectoplan/align")
def align_viewer_to_local(chat_id: str):
    """
    Erzwingt serverseitig die viewer_selection:
      1) Neueste **lokale** Version mit Speckle-IDs (Vorrang).
      2) Fallback: neueste Speckle-Version des Modells.
    """
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404

    # Projekt/Modell sicherstellen (und Cache aktualisieren)
    try:
        ensure_and_refresh(conv)
    except Exception:
        _log("warning", "align: ensure_and_refresh failed", exc_info=True)

    # 1) lokale Liste ziehen
    local = _collect_local_versions(conv.id)
    cand = _first_local_with_speckle(local)

    sel_pid = str(getattr(conv, "vectoplan_project_id", "") or "")
    sel_mid = str(getattr(conv, "vectoplan_model_id", "") or "")
    sel_vid = None
    source = "none"

    # 2) unsere Version hat Vorrang
    if cand:
        sp = (cand.get("speckle") or cand.get("meta", {}).get("speckle") or {}) if isinstance(cand, dict) else {}
        c_pid, c_mid, c_vid = str(sp.get("project_id") or ""), str(sp.get("model_id") or ""), str(sp.get("version_id") or "")
        if c_pid and c_mid and c_vid:
            spe_items = _speckle_versions(c_pid, c_mid, limit=20)
            server_ids = {_pick_id(v) for v in spe_items}
            if (c_vid in server_ids) or not spe_items:
                sel_pid, sel_mid, sel_vid, source = c_pid, c_mid, c_vid, "local"
            else:
                _log("warning", "align: local version_id %s not found on server; falling back", c_vid)

    # 3) Fallback → aktuellste Speckle-Version des Modells
    if not sel_vid and sel_pid and sel_mid:
        sv = _speckle_versions(sel_pid, sel_mid, limit=20)
        if sv:
            sel_vid = _pick_id(sv[0])
            source = "speckle"

    # 4) Auswahl speichern – Classmethod
    try:
        ConversationState.merge_patch(chat_id, {
            "viewer_selection": {
                "mode": "version" if sel_vid else "model",
                "project_id": sel_pid or None,
                "model_id": sel_mid or None,
                "version_id": sel_vid or None,
            }
        })
    except Exception:
        _log("warning", "align: save viewer_selection failed", exc_info=True)

    # 5) Viewer-URL (best effort)
    try:
        vurl = vp_viewer_url(conv) or ""
    except Exception:
        vurl = ""

    return jsonify({
        "status": "ok",
        "applied": {
            "project_id": sel_pid or None,
            "model_id": sel_mid or None,
            "version_id": sel_vid or None,
            "source": source,
        },
        "viewer_url": vurl or None,
    }), 200


@bp.post("/v1/chats/<chat_id>/vectoplan/purge-and-reupload")
def purge_and_reupload(chat_id: str):
    """
    Synchronisiert das Speckle-Modell mit der lokalen Versionshistorie:
      • Löscht Server-Versionen, die NICHT in der lokalen Historie vorkommen.
      • Lädt bis zu N (limit_reupload) neueste lokale Versionen erneut hoch,
        falls sie serverseitig fehlen und ein passender Blob (.ifc/.obj/.stl) existiert.

    Body JSON (optional): { "limit_reupload": 1 }
    """
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404

    try:
        ensure_and_refresh(conv)
    except Exception:
        _log("warning", "purge: ensure_and_refresh failed", exc_info=True)

    pid = str(getattr(conv, "vectoplan_project_id", "") or "")
    mid = str(getattr(conv, "vectoplan_model_id", "") or "")
    if not (pid and mid):
        return jsonify({"error": "project/model missing"}), 400

    body = request.get_json(silent=True) or {}
    limit_reupload = int(body.get("limit_reupload") or 1)
    if limit_reupload < 0:
        limit_reupload = 0

    # lokale Commit-IDs sammeln
    local_items = _collect_local_versions(conv.id)
    local_ids = _local_commit_ids(local_items)

    # 1) Server-Purge
    purge_report = _delete_server_versions_not_in(pid, mid, keep_ids=local_ids)

    # 2) Reupload falls gewünscht
    reup_report = _reupload_missing_local(conv, pid, mid, limit_reupload=limit_reupload)

    # 3) Viewer-Selektion erneut ausrichten (best effort)
    try:
        ConversationState.merge_patch(chat_id, {
            "viewer_selection": {
                "mode": "model",  # danach kann das Frontend neu ausrichten/auswählen
                "project_id": pid,
                "model_id": mid,
                "version_id": None,
            }
        })
    except Exception:
        pass

    # 4) Viewer-URL (optional)
    try:
        vurl = vp_viewer_url(conv) or ""
    except Exception:
        vurl = ""

    return jsonify({
        "status": "ok",
        "project_id": pid,
        "model_id": mid,
        "purge": purge_report,
        "reupload": reup_report,
        "viewer_url": vurl or None,
        "note": "Der Viewer zeigt ggf. noch die vorherige Auswahl an; die UI richtet beim nächsten View-Refresh neu aus.",
    }), 200
