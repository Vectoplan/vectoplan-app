# /services/app/routes/speckle_upload.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple, List

from flask import Blueprint, jsonify, request, current_app
from werkzeug.exceptions import BadRequest

from extensions import db
from models import Conversation, Blob
from vectoplan import ensure_and_refresh, viewer_url  # Upload-Fassade wird in vectoplan.py ergänzt
import messages as msg  # Karten/State-Helfer

bp = Blueprint("speckle_upload", __name__)

# Unterstützte Dateitypen
ALLOWED_EXTS = {".ifc", ".obj", ".stl"}
ALLOWED_MIMES = {
    "model/ifc", "application/octet-stream",  # IFC wird oft octet-stream geliefert
    "model/obj", "text/plain",                # OBJ kann text/plain sein
    "model/stl", "application/sla", "model/x.stl-binary",
}

# Maximal zu behaltende Versionen je Conversation und Kind
DEFAULT_KEEP_VERSIONS = 10


# ───────────────────────── Helpers ─────────────────────────

def _ext_of(filename: str) -> str:
    try:
        return os.path.splitext(filename or "")[1].lower()
    except Exception:
        return ""

def _validate_filetype(blob: Blob) -> Tuple[bool, str]:
    try:
        ext = _ext_of(blob.filename)
        if ext in ALLOWED_EXTS:
            return True, ext
        # Fallback über MIME
        mime = (blob.mime or "").lower()
        if mime in ALLOWED_MIMES:
            # Rate Extension
            if "ifc" in mime:
                return True, ".ifc"
            if "stl" in mime or "sla" in mime:
                return True, ".stl"
            if "obj" in mime:
                return True, ".obj"
        return False, ext
    except Exception:
        return False, ""

def _json_error(msg_text: str, code: int = 400):
    try:
        return jsonify({"error": msg_text}), code
    except Exception:
        return {"error": msg_text}, code

def _record_version_safe(conv: Conversation,
                         kind: str,
                         label: str,
                         source_message_id: Optional[str],
                         speckle_info: Dict[str, Any],
                         input_blob: Optional[Blob]) -> Dict[str, Any]:
    """
    Kapselt optionale Versionierung. Funktioniert, auch wenn 'versioning' noch nicht existiert.
    """
    try:
        # Lazy import, um harte Abhängigkeit zu vermeiden bis versioning.py existiert
        from versioning import record_version, prune

        meta: Dict[str, Any] = {"speckle": speckle_info}
        if input_blob:
            meta.update({
                "filename": input_blob.filename,
                "mime": input_blob.mime,
                "size": input_blob.size,
                "sha256": input_blob.sha256,
            })

        version_row = record_version(
            conversation_id=conv.id,
            kind=kind,
            label=label,
            source_message_id=source_message_id,
            input_blob_id=input_blob.id if input_blob else None,
            speckle_project_id=speckle_info.get("project_id"),
            speckle_model_id=speckle_info.get("model_id"),
            speckle_version_id=speckle_info.get("version_id"),
            status="ok",
            meta=meta,
        )

        # Pruning-Policy
        keep = int(current_app.config.get("KEEP_VERSIONS_PER_PROJECT", DEFAULT_KEEP_VERSIONS)) or DEFAULT_KEEP_VERSIONS
        try:
            prune(conversation_id=conv.id, kind=kind, keep=keep)
        except Exception:
            current_app.logger.warning("version prune failed", exc_info=True)

        return {
            "artifact_id": version_row.get("artifact_id"),
            "version_id": version_row.get("version_id"),
            "version_idx": version_row.get("version_idx"),
        }
    except Exception:
        # Versionierung nicht vorhanden oder fehlgeschlagen → nur loggen
        current_app.logger.warning("versioning not available or failed", exc_info=True)
        return {}

def _upload_file_to_speckle(conv: Conversation,
                            blob: Blob,
                            model_name: Optional[str],
                            ext: str) -> Dict[str, Any]:
    """
    Erwartet eine Fassade in vectoplan.py:
      upload_file_to_project(conv, blob, model_name=None, file_ext=".ifc"|".obj"|".stl") -> dict|None
    Rückgabe-Dict: {project_id, model_id, version_id, viewer_url}
    """
    try:
        from vectoplan import upload_file_to_project  # wird in vectoplan.py implementiert
    except Exception as ex:
        current_app.logger.error("vectoplan upload facade missing: %s", ex)
        raise BadRequest("upload not implemented on server")

    res = upload_file_to_project(conv=conv, blob=blob, model_name=model_name, file_ext=ext)
    if not isinstance(res, dict):
        raise BadRequest("upload failed")
    return res

def _post_card_safe(conv: Conversation, template_key: str, payload: Dict[str, Any], trace: Optional[List[str]] = None):
    try:
        msg.post_card_message(conversation=conv,
                              template_key=template_key,
                              payload=payload or {},
                              role="service",
                              trace=trace or ["BGA"],
                              validate=False)
        try:
            db.session.add(conv); db.session.commit()
        except Exception:
            db.session.rollback()
    except Exception:
        current_app.logger.warning("post_card_safe failed", exc_info=True)

def _post_error_card(conv: Optional[Conversation], text: str):
    try:
        if not conv:
            return
        _post_card_safe(conv, "error_card", {"title": "Upload-Fehler", "md": text[:800]})
    except Exception:
        current_app.logger.warning("post_error_card failed", exc_info=True)


# ───────────────────────── Endpoints ─────────────────────────

@bp.post("/v1/chats/<chat_id>/speckle/upload")
def upload_geometry_file(chat_id: str):
    """
    JSON: { "file_id": "<Blob.id>", "model_name": "optional", "source_message_id": "optional" }
    Unterstützt: .ifc .obj .stl
    Antwort: { status, chat_id, speckle:{...}, version:{...}, viewer_url }
    """
    conv: Optional[Conversation] = None
    try:
        data = request.get_json(silent=True) or {}
        file_id = str(data.get("file_id") or "").strip()
        model_name = str(data.get("model_name") or "").strip() or None
        source_message_id = str(data.get("source_message_id") or "").strip() or None
        if not file_id:
            return _json_error("file_id required", 400)

        conv = Conversation.query.get(chat_id)
        if not conv:
            return _json_error("chat not found", 404)

        blob = Blob.query.get(file_id)
        if not blob:
            _post_error_card(conv, "Datei nicht gefunden.")
            return _json_error("file not found", 404)

        ok, ext = _validate_filetype(blob)
        if not ok:
            _post_error_card(conv, f"Nicht unterstützter Dateityp: {ext or blob.mime}")
            return _json_error(f"unsupported file type: {ext or blob.mime}", 415)

        # Speckle-Projekt sicherstellen
        try:
            ensure_and_refresh(conv)
        except Exception:
            current_app.logger.warning("ensure_and_refresh failed", exc_info=True)

        # Datei hochladen
        try:
            speckle_info = _upload_file_to_speckle(conv, blob, model_name, ext)
            # 🔎 Debug: sofort sehen, was aus upload_file_to_project zurückkommt
            current_app.logger.debug(
                "speckle_upload: chat=%s project=%s model=%s version=%s viewer=%s",
                conv.id,
                speckle_info.get("project_id"),
                speckle_info.get("model_id"),
                speckle_info.get("version_id"),
                speckle_info.get("viewer_url"),
            )
        except BadRequest as br:
            _post_error_card(conv, str(br))
            return _json_error(str(br), 400)
        except Exception as ex:
            current_app.logger.exception("upload failed")
            _post_error_card(conv, f"Upload-Fehler: {ex}")
            return _json_error(f"upload error: {ex}", 502)

        # Viewer-URL ableiten oder übernehmen
        vurl = speckle_info.get("viewer_url") or ""
        if not vurl:
            try:
                # Fallback: Cache aktualisieren und Standard-Viewer-URL bauen
                ensure_and_refresh(conv)
                vurl = viewer_url(conv) or ""
            except Exception:
                vurl = ""

        # Versionierung
        try:
            # IFC → BGA_IFC, OBJ/STL → BGA_MESH
            kind = "BGA_IFC" if ext == ".ifc" else "BGA_MESH"
            version_info = _record_version_safe(
                conv=conv,
                kind=kind,
                label=os.path.basename(blob.filename or "upload"),
                source_message_id=source_message_id,
                speckle_info=speckle_info,
                input_blob=blob,
            )
        except Exception:
            version_info = {}

        # Persistente Änderungen an Conversation speichern, falls gesetzt
        try:
            db.session.add(conv)
            db.session.commit()
        except Exception:
            current_app.logger.warning("DB commit after upload failed", exc_info=True)

        # Karte mit Viewer-Link posten (optional, aber default sinnvoll)
        try:
            if vurl:
                _post_card_safe(conv, "speckle_viewer", {"url": vurl, "caption": "Neue Version geladen"}, trace=["BGA"])
        except Exception:
            current_app.logger.warning("post speckle_viewer card failed", exc_info=True)

        return jsonify({
            "status": "ok",
            "chat_id": conv.id,
            "speckle": {
                "project_id": speckle_info.get("project_id"),
                "model_id": speckle_info.get("model_id"),
                "version_id": speckle_info.get("version_id"),
            },
            "version": version_info or {},
            "viewer_url": vurl,
            "filename": blob.filename,
            "size": blob.size,
        }), 200

    except Exception as ex:
        current_app.logger.exception("upload_geometry_file failed")
        _post_error_card(conv, f"Interner Fehler: {ex}")
        return _json_error(str(ex), 500)


@bp.get("/v1/chats/<chat_id>/versions")
def list_versions(chat_id: str):
    """
    Liefert eine flache Liste der letzten Versionen pro Artefakt-Kind.
    Optional Query-Parameter: kind=BGA_IFC|BGA_MESH|DAA|BPA_2D|FPA_SEED
    """
    conv = Conversation.query.get(chat_id)
    if not conv:
        return _json_error("chat not found", 404)

    kind = request.args.get("kind") or None
    try:
        from versioning import list_versions_by_conversation
    except Exception:
        # Versionierung noch nicht vorhanden
        return jsonify({"items": [], "total": 0}), 200

    try:
        items = list_versions_by_conversation(conversation_id=conv.id, kind=kind)
        return jsonify({"items": items or [], "total": len(items or [])}), 200
    except Exception as ex:
        current_app.logger.exception("list_versions failed")
        return _json_error(f"list error: {ex}", 500)


@bp.delete("/v1/chats/<chat_id>/versions/<version_id>")
def delete_version(chat_id: str, version_id: str):
    """
    Entfernt eine Version inkl. Kanten. Blobs werden nur gelöscht, wenn nicht mehr referenziert.
    """
    conv = Conversation.query.get(chat_id)
    if not conv:
        return _json_error("chat not found", 404)

    try:
        from versioning import delete_version
    except Exception:
        return _json_error("versioning not available", 503)

    try:
        ok = delete_version(version_id=version_id)
        if not ok:
            return _json_error("version not found", 404)
        return jsonify({"status": "ok"}), 200
    except Exception as ex:
        current_app.logger.exception("delete_version failed")
        return _json_error(f"delete error: {ex}", 500)
