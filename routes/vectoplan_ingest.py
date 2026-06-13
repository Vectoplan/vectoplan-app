# /services/app/routes/vectoplan_ingest.py
from __future__ import annotations

"""
Vectoplan Ingest (robust)

Aufgaben:
- Projekt/Modell sicherstellen (ggf. Platzhalter)
- Dateien als Version ins Speckle/Vectoplan-Projekt hochladen
- Commit-Messages konsistent setzen (entsprechen lokalen Labels)
- Lokale Version (versioning) anlegen, Model optional umbenennen
- Diagnose: Abgleich Server- vs. lokale Liste (IDs & Messages)
- Viewer-Selektion nach Upload auf die neue Version setzen
- **Repair**: Lokale Versionen ohne Speckle-ID automatisch nachtragen

Routen:
- POST /v1/chats/<chat_id>/vectoplan/ensure[?with_test=1]
- POST /v1/chats/<chat_id>/vectoplan/upload   { blob_id, model_name? }
- POST /v1/chats/<chat_id>/vectoplan/test-upload
- POST /v1/chats/<chat_id>/vectoplan/repair   { limit? }  → scannt/fixt fehlende Speckle-IDs
"""

from typing import Dict, Any, Tuple, Optional, List
from urllib.parse import quote

from flask import Blueprint, request, jsonify, current_app, url_for
from models import Conversation, Blob, ConversationState
from vectoplan import (
    ensure_and_refresh,
    ensure_placeholder_if_empty,
    upload_file_to_project,   # liefert commit_message, version_id, project_id, model_id
    viewer_url as vp_viewer_url,
)

bp = Blueprint("vectoplan_ingest", __name__)

# ───────────────────────── Mini HTTP-Client (lokal) ─────────────────────────

def _cfg() -> Tuple[str, Optional[str]]:
    try:
        host = (current_app.config.get("VECTOPLAN_HOST", "https://vectoplan.com") or "").rstrip("/")
        token = current_app.config.get("VECTOPLAN_TOKEN")
        return host, token
    except Exception:
        return "https://vectoplan.com", None

def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}

def _url(host: str, path: str) -> str:
    p = (path or "").lstrip("/")
    return f"{host}/{p}"

def _gql(query: str, variables: Optional[Dict[str, Any]] = None, timeout_s: int = 15) -> Optional[Dict[str, Any]]:
    import requests
    host, token = _cfg()
    if not token:
        return None
    try:
        r = requests.post(_url(host, "graphql"), json={"query": query, "variables": variables or {}}, headers=_headers(token), timeout=timeout_s)
        if r.status_code >= 400:
            current_app.logger.error("Vectoplan GraphQL HTTP %s: %s", r.status_code, (r.text or "")[:300])
            return None
        data = r.json() or {}
        if data.get("errors"):
            current_app.logger.error("Vectoplan GraphQL errors: %s", data["errors"])
            return None
        return data.get("data")
    except Exception as ex:
        current_app.logger.error("Vectoplan GraphQL exception: %s", ex)
        return None

def _rest_get(path: str, *, params: Optional[Dict[str, Any]] = None, timeout_s: int = 15) -> Tuple[int, Optional[Dict[str, Any]]]:
    import requests
    host, token = _cfg()
    if not token:
        return 401, None
    try:
        r = requests.get(_url(host, path), params=params or {}, headers=_headers(token), timeout=timeout_s)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, None
    except Exception as ex:
        current_app.logger.error("Vectoplan REST GET %s failed: %s", path, ex)
        return 599, None

def _rest_post_json(path: str, *, payload: Dict[str, Any], timeout_s: int = 15) -> Tuple[int, Optional[Dict[str, Any]]]:
    import requests
    host, token = _cfg()
    if not token:
        return 401, None
    try:
        r = requests.post(_url(host, path), json=payload or {}, headers=_headers(token), timeout=timeout_s)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, None
    except Exception as ex:
        current_app.logger.error("Vectoplan REST POST %s failed: %s", path, ex)
        return 599, None

def _rest_patch_json(path: str, *, payload: Dict[str, Any], timeout_s: int = 15) -> Tuple[int, Optional[Dict[str, Any]]]:
    import requests
    host, token = _cfg()
    if not token:
        return 401, None
    try:
        r = requests.patch(_url(host, path), json=payload or {}, headers=_headers(token), timeout=timeout_s)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, None
    except Exception as ex:
        current_app.logger.error("Vectoplan REST PATCH %s failed: %s", path, ex)
        return 599, None

def _rest_put_json(path: str, *, payload: Dict[str, Any], timeout_s: int = 15) -> Tuple[int, Optional[Dict[str, Any]]]:
    import requests
    host, token = _cfg()
    if not token:
        return 401, None
    try:
        r = requests.put(_url(host, path), json=payload or {}, headers=_headers(token), timeout=timeout_s)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, None
    except Exception as ex:
        current_app.logger.error("Vectoplan REST PUT %s failed: %s", path, ex)
        return 599, None

# ───────────────────────── Helpers ─────────────────────────

ALLOWED_EXTS = {".ifc", ".obj", ".stl"}

def _ext_of(filename: str) -> str:
    try:
        import os
        return os.path.splitext(filename or "")[1].lower()
    except Exception:
        return ""

def _proxied_viewer_url(vurl: str) -> str:
    try:
        return url_for("embed.proxy_frame", url=quote(vurl, safe="")) if vurl else ""
    except Exception:
        return ""

def _sanitize_label(label: str) -> str:
    try:
        base = (label or "version").strip()
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in base)
        return safe[:120] or "version"
    except Exception:
        return "version"

def _record_version_safe(conv: Conversation, kind: str, label: str, speckle_info: dict, blob: Optional[Blob]) -> Optional[str]:
    """
    Legt lokal eine Version an (best effort). 'label' sollte identisch zur Speckle-Commit-Message sein.
    """
    try:
        from versioning import record_version, prune
        meta = {"speckle": dict(speckle_info or {})}
        if blob:
            meta.update({"filename": blob.filename, "mime": blob.mime, "size": blob.size, "sha256": blob.sha256})
        row = record_version(
            conversation_id=conv.id,
            kind=kind,
            label=_sanitize_label(label),
            source_message_id=None,
            input_blob_id=(blob.id if blob else None),
            speckle_project_id=speckle_info.get("project_id"),
            speckle_model_id=speckle_info.get("model_id"),
            speckle_version_id=speckle_info.get("version_id"),
            status="ok",
            meta=meta,
        )
        keep = int(current_app.config.get("KEEP_VERSIONS_PER_PROJECT", 10)) or 10
        try:
            prune(conversation_id=conv.id, kind=kind, keep=keep)
        except Exception:
            current_app.logger.warning("version prune failed", exc_info=True)
        return row.get("label") or label
    except Exception:
        current_app.logger.warning("versioning not available or failed", exc_info=True)
        return None

def _q(s: str) -> str:
    try:
        import requests
        return requests.utils.quote(s, safe="")
    except Exception:
        return s

def _rename_model(project_id: str, model_id: str, new_name: str) -> bool:
    nn = _sanitize_label(new_name)[:80] or "model"
    for fn, path in (
        (_rest_patch_json, f"api/projects/{project_id}/models/{model_id}"),
        (_rest_put_json,   f"api/projects/{project_id}/models/{model_id}"),
        (_rest_post_json,  f"api/projects/{project_id}/models/{model_id}/rename"),
    ):
        try:
            status, _ = fn(path, payload={"name": nn})
            if status in (200, 201, 202):
                return True
        except Exception:
            pass
    try:
        q = "mutation ($id:String!,$name:String!){ modelUpdate(id:$id,name:$name) }"
        d = _gql(q, {"id": model_id, "name": nn})
        if d:
            return True
    except Exception:
        pass
    current_app.logger.warning("model rename failed for %s/%s -> %s", project_id, model_id, nn)
    return False

# ───────────────────────── Speckle test commits (mit Message) ─────────────────

def _commit_create_graphql(stream_or_model_id: str, object_id: str, message: str) -> Optional[str]:
    p = {"streamId": stream_or_model_id, "objectId": object_id, "branchName": "main",
         "message": message, "sourceApplication": "vectoplan-ingest"}
    # alte + neue Signatur
    for q, key in (
        ("mutation ($commit: CommitCreateInput!) { commitCreate(commit: $commit) }", "commit"),
        ("mutation ($input: CommitCreateInput!) { commitCreate(input: $input) }", "input"),
    ):
        try:
            data = _gql(q, {key: p})
            if data and data.get("commitCreate"):
                v = data["commitCreate"]
                return str(v[0] if isinstance(v, list) and v else v)
        except Exception:
            pass
    return None

def _rest_post_commit(stream_or_model_id: str, object_id: str, message: str) -> Optional[str]:
    status, js = _rest_post_json(
        f"api/streams/{_q(stream_or_model_id)}/commits",
        payload={
            "streamId": stream_or_model_id,
            "objectId": object_id,
            "branchName": "main",
            "message": message,
            "sourceApplication": "vectoplan-ingest",
        },
    )
    if status in (200, 201) and isinstance(js, dict):
        return js.get("id") or js.get("commitId") or js.get("versionId")
    return None

def _object_create_graphql(stream_id: str, obj: dict) -> Optional[str]:
    q = "mutation ($input:ObjectCreateInput!){ objectCreate(objectInput:$input) }"
    d = _gql(q, {"input": {"streamId": stream_id, "objects": [obj]}})
    if not d:
        return None
    v = d.get("objectCreate")
    if isinstance(v, list) and v:
        return str(v[0])
    if isinstance(v, str):
        return v
    return None

def _object_create_rest_stream(stream_id: str, obj: dict) -> Optional[str]:
    status, js = _rest_post_json(f"api/streams/{_q(stream_id)}/objects", payload={"objects": [obj]})
    if status in (200, 201) and isinstance(js, dict):
        arr = js.get("objects") or []
        if arr and isinstance(arr[0], dict):
            return arr[0].get("id")
    return None

def _object_create_rest_generic(stream_id: str, obj: dict) -> Optional[str]:
    status, js = _rest_post_json(f"api/objects?streamId={_q(stream_id)}", payload={"objects": [obj]})
    if status in (200, 201) and isinstance(js, dict):
        arr = js.get("objects") or []
        if arr and isinstance(arr[0], dict):
            return arr[0].get("id")
    return None

def _create_object_any(stream_or_model_id: str, obj: dict) -> Optional[str]:
    return (_object_create_graphql(stream_or_model_id, obj)
            or _object_create_rest_stream(stream_or_model_id, obj)
            or _object_create_rest_generic(stream_or_model_id, obj))

def _timestamp() -> str:
    try:
        from datetime import datetime
        return datetime.now().strftime("%d.%m.%Y__%H.%M")
    except Exception:
        return "00.00.0000__00.00"

def _commit_test_geometry_on_model(project_id: str, model_id: str) -> Optional[Dict[str, str]]:
    """
    Erzeugt Testgeometrie und committet sie mit einer **sprechenden Message**,
    die wir 1:1 als lokalen Versions-Label verwenden (Alignment).
    """
    candidates = [model_id, project_id]  # erst Modell, dann Projekt/Stream

    def _commit(obj: dict, base_label: str) -> Optional[Dict[str, str]]:
        for target in candidates:
            oid = _create_object_any(target, obj)
            if not oid:
                continue
            label = f"{base_label}__{_timestamp()}"
            cid = (_commit_create_graphql(target, oid, label) or _rest_post_commit(target, oid, label))
            if cid:
                return {"label": label, "commit_id": cid, "target": target}
        return None

    # 1) Mesh
    mesh = {"speckle_type": "Objects.Geometry.Mesh", "units": "m",
            "vertices": [0,0,0, 1,0,0, 0,1,0], "faces": [0, 0,1,2]}
    res = _commit(mesh, "test-mesh")
    if res:
        return {"kind": "BGA_MESH", **res}

    # 2) Polyline
    poly = {"speckle_type": "Objects.Geometry.Polyline", "units": "m",
            "value": [0,0,0, 2,0,0, 2,2,0, 0,0,0], "closed": False}
    res = _commit(poly, "test-polyline")
    if res:
        return {"kind": "BGA_CURVE", **res}

    # 3) Point
    pt = {"speckle_type": "Objects.Geometry.Point", "x": 0.0, "y": 0.0, "z": 0.0, "units": "m"}
    res = _commit(pt, "test-point")
    if res:
        return {"kind": "BGA_POINT", **res}

    return None

# ───────────────────────── Alignment / Diagnose ─────────────────────────

def _list_model_versions(pid: str, mid: str, limit: int = 10) -> List[Dict[str, Any]]:
    status, js = _rest_get(f"api/projects/{pid}/models/{mid}/versions", params={"limit": limit})
    if status in (200, 201) and isinstance(js, dict):
        return [x for x in (js.get("items") or js.get("versions") or []) if isinstance(x, dict)]
    return []

def _pick_id(v: Dict[str, Any]) -> Optional[str]:
    try:
        return v.get("id") or v.get("versionId") or v.get("commitId")
    except Exception:
        return None

def _verify_alignment(pid: str, mid: str, final_vid: Optional[str], expect_label: Optional[str]) -> None:
    try:
        vers = _list_model_versions(pid, mid, limit=10)
        ids = [(_pick_id(x) or "") for x in vers]
        msgs = [str(x.get("message") or "") for x in vers]
        current_app.logger.info("ingest.verify: server_top_ids=%s messages=%s final=%s expect_label=%s",
                                ids, msgs, final_vid, expect_label)
        if expect_label and expect_label not in msgs:
            current_app.logger.warning("ingest.verify: expected label not found in server messages")
    except Exception as ex:
        current_app.logger.warning("ingest.verify failed: %s", ex, exc_info=True)

# ───────────────────────── Projekt/Modell sicherstellen ──────────────────────

def _ensure_project_model_and_placeholder(conv: Conversation) -> Tuple[str, str, str]:
    try:
        ensure_and_refresh(conv)
    except Exception:
        current_app.logger.warning("vectoplan ensure/refresh failed", exc_info=True)

    vurl = ""
    try:
        vurl = vp_viewer_url(conv) or ""
        if not vurl and getattr(conv, "vectoplan_project_id", None):
            ensure_placeholder_if_empty(conv)
            vurl = vp_viewer_url(conv) or ""
    except Exception:
        current_app.logger.warning("placeholder ensure failed", exc_info=True)

    if not getattr(conv, "vectoplan_project_id", None) or not getattr(conv, "vectoplan_model_id", None):
        raise RuntimeError("project/model not available")

    return str(conv.vectoplan_project_id), str(conv.vectoplan_model_id), vurl

def _kind_from_ext(ext: str) -> str:
    return "BGA_IFC" if ext == ".ifc" else "BGA_MESH"

def _upload_blob_as_version(conv: Conversation, blob: Blob, model_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Lädt Blob als neue Version hoch. Nutzt die vom Upload zurückgegebene commit_message
    als lokales Label → dadurch **Namensgleichheit** in beiden Listen.
    """
    ext = _ext_of(blob.filename)
    if ext not in ALLOWED_EXTS:
        raise RuntimeError(f"unsupported file type: {ext or blob.mime}")

    info = upload_file_to_project(conv=conv, blob=blob, model_name=model_name, file_ext=ext) or {}
    kind = _kind_from_ext(ext)
    try:
        label = info.get("commit_message") or (blob.filename or "upload")
        _record_version_safe(conv=conv, kind=kind, label=label, speckle_info=info, blob=blob)
    except Exception:
        pass
    return info

# ───────────────────────── Routes ─────────────────────────

@bp.post("/v1/chats/<chat_id>/vectoplan/ensure")
def ensure_route(chat_id: str):
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404
    try:
        pid, mid, vurl = _ensure_project_model_and_placeholder(conv)
    except Exception as ex:
        current_app.logger.exception("ensure failed")
        return jsonify({"error": str(ex)}), 500

    test_flag = (request.args.get("with_test") == "1") or bool((request.json or {}).get("with_test"))
    test_result: Dict[str, Any] = {}
    if test_flag:
        try:
            res = _commit_test_geometry_on_model(pid, mid)
            if res:
                try:
                    lab = _record_version_safe(
                        conv=conv,
                        kind=res["kind"],
                        label=res["label"],  # identisch zur Commit-Message
                        speckle_info={"project_id": pid, "model_id": mid, "version_id": res["commit_id"]},
                        blob=None,
                    )
                    if lab:
                        _rename_model(pid, mid, lab)
                except Exception:
                    lab = None
                test_result = {
                    "commit_id": res["commit_id"],
                    "kind": res["kind"],
                    "new_label": res["label"],
                }
                _verify_alignment(pid, mid, res["commit_id"], res["label"])
            else:
                test_result = {"error": "commit failed"}
        except Exception as ex:
            current_app.logger.warning("test commit failed: %s", ex, exc_info=True)
            test_result = {"error": str(ex)}

    return jsonify({
        "chat_id": conv.id,
        "project_id": pid,
        "model_id": mid,
        "viewer_url": _proxied_viewer_url(vurl),
        "raw_viewer_url": vurl,
        "test": test_result,
    }), 200


@bp.post("/v1/chats/<chat_id>/vectoplan/upload")
def upload_route(chat_id: str):
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(silent=True) or {}
    blob_id = str(data.get("blob_id") or "").strip()
    model_name = (data.get("model_name") or None)
    if not blob_id:
        return jsonify({"error": "blob_id required"}), 400

    b = Blob.query.get(blob_id)
    if not b:
        return jsonify({"error": "blob not found"}), 404

    try:
        pid, mid, _ = _ensure_project_model_and_placeholder(conv)
    except Exception as ex:
        current_app.logger.exception("ensure before upload failed")
        return jsonify({"error": str(ex)}), 500

    try:
        info = _upload_blob_as_version(conv, b, model_name=model_name)
        vurl = vp_viewer_url(conv) or ""
        label_used = info.get("commit_message") or (b.filename or "upload")

        try:
            if pid and mid:
                _rename_model(pid, mid, label_used)
        except Exception:
            pass

        try:
            _verify_alignment(pid, mid, info.get("version_id"), label_used)
        except Exception:
            pass

        # Viewer-Auswahl auf die neue Version setzen
        try:
            if info.get("project_id") and info.get("model_id") and info.get("version_id"):
                ConversationState.merge_patch(conv.id, {
                    "viewer_selection": {
                        "mode": "version",
                        "project_id": info["project_id"],
                        "model_id": info["model_id"],
                        "version_id": info["version_id"],
                    }
                })
        except Exception:
            current_app.logger.warning("ingest.upload: save viewer_selection failed", exc_info=True)

        return jsonify({
            "chat_id": conv.id,
            "file_id": b.id,
            "project_id": info.get("project_id") or getattr(conv, "vectoplan_project_id", None),
            "model_id": info.get("model_id") or getattr(conv, "vectoplan_model_id", None),
            "version_id": info.get("version_id"),
            "viewer_url": _proxied_viewer_url(vurl),
            "raw_viewer_url": vurl,
            "label": label_used,
        }), 201
    except Exception as ex:
        current_app.logger.exception("upload failed")
        return jsonify({"error": str(ex)}), 500


@bp.post("/v1/chats/<chat_id>/vectoplan/test-upload")
def test_upload_route(chat_id: str):
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404
    try:
        pid, mid, vurl = _ensure_project_model_and_placeholder(conv)
        res = _commit_test_geometry_on_model(pid, mid)
        if not res:
            return jsonify({"error": "commit failed"}), 500

        try:
            lab = _record_version_safe(
                conv=conv,
                kind=res["kind"],
                label=res["label"],  # identisch zur Speckle-Message
                speckle_info={"project_id": pid, "model_id": mid, "version_id": res["commit_id"]},
                blob=None,
            )
            if lab:
                _rename_model(pid, mid, lab)
        except Exception:
            lab = res["label"]

        _verify_alignment(pid, mid, res["commit_id"], res["label"])

        # Viewer-Auswahl auf den Test-Commit setzen
        try:
            ConversationState.merge_patch(conv.id, {
                "viewer_selection": {
                    "mode": "version",
                    "project_id": pid,
                    "model_id": mid,
                    "version_id": res["commit_id"],
                }
            })
        except Exception:
            current_app.logger.warning("ingest.test-upload: save viewer_selection failed", exc_info=True)

        return jsonify({
            "chat_id": conv.id,
            "commit_id": res["commit_id"],
            "kind": res["kind"],
            "label": res["label"],
            "viewer_url": _proxied_viewer_url(vurl),
            "raw_viewer_url": vurl,
        }), 201
    except Exception as ex:
        current_app.logger.exception("test-upload failed")
        return jsonify({"error": str(ex)}), 500


# ───────────────────────── Repair: lokale Versionen ohne Speckle-ID fixen ─────────────────────────

def _load_local_versions(conv_id: str) -> List[Dict[str, Any]]:
    try:
        from versioning import list_versions_by_conversation
        return list_versions_by_conversation(conversation_id=conv_id, kind=None) or []
    except Exception:
        return []

def _extract_speckle_meta(v: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sp = (v.get("speckle") or v.get("meta", {}).get("speckle") or {}) if isinstance(v, dict) else {}
        return {
            "project_id": sp.get("project_id"),
            "model_id":   sp.get("model_id"),
            "version_id": sp.get("version_id"),
        }
    except Exception:
        return {"project_id": None, "model_id": None, "version_id": None}

@bp.post("/v1/chats/<chat_id>/vectoplan/repair")
def repair_missing_speckle(chat_id: str):
    """
    Sucht lokale Versionen **ohne** Speckle-IDs und lädt deren Blobs als Version hoch.
    - Nur .ifc/.obj/.stl (andere werden übersprungen).
    - Für jede reparierte Version wird eine **neue** lokale Version mit Label=commit_message angelegt
      (und der Alt-Eintrag best-effort auf 'superseded' gesetzt, falls Update-API vorhanden).
    Rückgabe: { fixed, skipped, errors:[], items:[...] }
    """
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404

    # Projekt/Modell bereit
    try:
        pid, mid, _ = _ensure_project_model_and_placeholder(conv)
    except Exception as ex:
        current_app.logger.exception("repair: ensure failed")
        return jsonify({"error": str(ex)}), 500

    limit = 100
    try:
        limit = int(request.args.get("limit") or (request.json or {}).get("limit") or 100)
        limit = max(1, min(limit, 500))
    except Exception:
        limit = 100

    local = _load_local_versions(conv.id)[:limit]
    fixed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    # Funktionen aus versioning (optional)
    try:
        from versioning import update_version_status  # type: ignore
    except Exception:
        update_version_status = None  # type: ignore

    for v in local:
        try:
            sp = _extract_speckle_meta(v)
            has_ids = bool(sp.get("project_id") and sp.get("model_id") and sp.get("version_id"))
            blob_id = v.get("input_blob_id")
            label   = str(v.get("label") or "").strip()
            kind    = str(v.get("kind") or "").strip() or None

            if has_ids:
                skipped.append({"reason": "already_has_speckle", "version_id": v.get("version_id"), "label": label})
                continue
            if not blob_id:
                skipped.append({"reason": "no_blob", "version_id": v.get("version_id"), "label": label})
                continue

            b = Blob.query.get(blob_id)
            if not b:
                skipped.append({"reason": "blob_missing", "version_id": v.get("version_id"), "label": label})
                continue

            ext = _ext_of(b.filename)
            if ext not in ALLOWED_EXTS:
                skipped.append({"reason": "unsupported_ext", "ext": ext, "label": label})
                continue

            # Upload (neue Speckle-Version)
            info = upload_file_to_project(conv=conv, blob=b, model_name=None, file_ext=ext) or {}
            used_label = info.get("commit_message") or (label or b.filename or "upload")
            new_kind = kind or _kind_from_ext(ext)

            # Lokale neue Version anlegen (best effort)
            try:
                _record_version_safe(conv=conv, kind=new_kind, label=used_label, speckle_info=info, blob=b)
            except Exception:
                pass

            # Alt-Row best-effort als 'superseded' markieren
            try:
                if update_version_status and v.get("version_id"):
                    update_version_status(version_id=v["version_id"], status="superseded")  # type: ignore
            except Exception:
                pass

            fixed.append({
                "old_local_version_id": v.get("version_id"),
                "old_label": label,
                "blob_id": b.id,
                "speckle_project_id": info.get("project_id"),
                "speckle_model_id": info.get("model_id"),
                "speckle_version_id": info.get("version_id"),
                "new_label": used_label,
            })

        except Exception as ex_item:
            current_app.logger.warning("repair: item failed: %s", ex_item, exc_info=True)
            errors.append({"error": str(ex_item), "old_version_id": v.get("version_id")})

    # Viewer auf jüngste gefixte Version setzen (falls vorhanden)
    try:
        if fixed:
            last = fixed[-1]
            if last.get("speckle_project_id") and last.get("speckle_model_id") and last.get("speckle_version_id"):
                ConversationState.merge_patch(conv.id, {
                    "viewer_selection": {
                        "mode": "version",
                        "project_id": last["speckle_project_id"],
                        "model_id":   last["speckle_model_id"],
                        "version_id": last["speckle_version_id"],
                    }
                })
    except Exception:
        current_app.logger.warning("repair: save viewer_selection failed", exc_info=True)

    summary = {
        "fixed": len(fixed),
        "skipped": len(skipped),
        "errors": len(errors),
        "items": fixed,
        "skipped_items": skipped[:20],  # Antwort klein halten
        "error_items": errors[:20],
        "project_id": getattr(conv, "vectoplan_project_id", None),
        "model_id": getattr(conv, "vectoplan_model_id", None),
    }
    current_app.logger.info("[repair] summary: %s", summary)

    return jsonify(summary), 200
