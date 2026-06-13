# /services/app/routes/ui/chat.py
from __future__ import annotations

import os
from urllib.parse import quote
from typing import Dict, Any, List, Tuple, Optional

from flask import (
    Blueprint,
    render_template,
    request,
    current_app,
    make_response,
    redirect,
    url_for,
    jsonify,
)

from extensions import db
from models import Conversation, ConversationState, Blob  # ⬅️ ConversationState ergänzt
from vectoplan import ensure_and_refresh, viewer_url, ensure_placeholder_if_empty
import messages as msg  # Templates & Cards


bp = Blueprint("ui_chat", __name__)

# ───────────────────────── helpers ─────────────────────────

# 3D-Uploads (Speckle) + 2D-Plan (DXF)
ALLOWED_EXTS = {".ifc", ".obj", ".stl", ".dxf"}
ALLOWED_MIMES = {
    # IFC
    "model/ifc", "application/ifc", "application/x-ifc", "application/octet-stream",
    # OBJ
    "model/obj", "text/plain",
    # STL
    "model/stl", "application/sla", "model/x.stl-binary",
    # DXF
    "application/dxf", "application/x-dxf", "image/vnd.dxf", "image/x-dxf",
}


def _is_dev() -> bool:
    try:
        env = str(current_app.config.get("FLASK_ENV", "") or "").lower()
        return env.startswith("dev") or env.startswith("development")
    except Exception:
        return False


def _apply_security_headers(resp) -> None:
    """
    Minimal, robust. Keine harten CSPs, da Viewer/iframes dynamisch sind.
    """
    try:
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
    except Exception:
        pass
    try:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    except Exception:
        pass


def _apply_cache_headers(resp, *, no_store: bool = False) -> None:
    """
    no_store=True erzwingt no-store.
    In DEV wird standardmäßig no-store gesetzt.
    """
    try:
        if no_store or _is_dev():
            resp.headers["Cache-Control"] = "no-store"
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
    except Exception:
        pass


def _apply_frame_headers(resp) -> None:
    """
    Standard: SAMEORIGIN.
    Optional per ?allow_embed=1: frame-ancestors * und XFO entfernen.
    """
    try:
        if request.args.get("allow_embed") == "1":
            resp.headers["Content-Security-Policy"] = "frame-ancestors *"
            try:
                resp.headers.pop("X-Frame-Options", None)
            except Exception:
                pass
        else:
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    except Exception:
        pass


def _finalize_html_response(resp, *, no_store: bool = False) -> None:
    try:
        _apply_security_headers(resp)
    except Exception:
        pass
    try:
        _apply_cache_headers(resp, no_store=no_store)
    except Exception:
        pass
    try:
        _apply_frame_headers(resp)
    except Exception:
        pass


def _finalize_json_response(resp, *, no_store: bool = True) -> None:
    # JSON sollte im UI-Kontext i. d. R. nicht aggressiv gecacht werden
    try:
        _apply_security_headers(resp)
    except Exception:
        pass
    try:
        _apply_cache_headers(resp, no_store=no_store)
    except Exception:
        pass


def _simple_html_error(message: str, code: int = 500):
    """
    Robuster HTML-Fallback für Iframe-Targets (besser als JSON im Viewer-Frame).
    """
    try:
        safe = (message or "error")[:1000]
    except Exception:
        safe = "error"
    html = (
        "<!doctype html><html lang='de'><head>"
        "<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{code}</title></head>"
        "<body style='font:14px/1.4 system-ui,Segoe UI,Roboto,Arial;margin:24px;'>"
        f"<h1 style='margin:0 0 10px 0;'>{code}</h1>"
        f"<p style='margin:0;color:#444;'>{safe}</p>"
        "</body></html>"
    )
    resp = make_response(html, code)
    try:
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
    except Exception:
        pass
    _finalize_html_response(resp, no_store=True)
    return resp


def _ext_of(filename: str) -> str:
    try:
        return os.path.splitext(filename or "")[1].lower()
    except Exception:
        return ""


def _validate_filetype(filename: str, mimetype: Optional[str]) -> Tuple[bool, str]:
    """
    Ermittelt erlaubte Endung.
    Priorität: Dateiendung > MIME-Heuristik.
    """
    try:
        ext = _ext_of(filename)
        if ext in ALLOWED_EXTS:
            return True, ext
        mime = (mimetype or "").lower()
        if mime in ALLOWED_MIMES:
            if "ifc" in mime:
                return True, ".ifc"
            if "stl" in mime or "sla" in mime:
                return True, ".stl"
            if "obj" in mime:
                return True, ".obj"
            if "dxf" in mime or "vnd.dxf" in mime or "x-dxf" in mime:
                return True, ".dxf"
        return False, ext
    except Exception:
        return False, ""


def _proxied_viewer_url(vurl: str) -> str:
    try:
        return url_for("embed.proxy_frame", url=quote(vurl, safe="")) if vurl else ""
    except Exception:
        return ""


def _file_urls(file_id: str) -> Dict[str, str]:
    """Relative URLs wie /v1/files/<id>/... für Frontend-Kompatibilität."""
    try:
        base = ""
        fid = quote(str(file_id), safe="")
        return {
            "content_url": f"{base}/v1/files/{fid}/content",
            "download_url": f"{base}/v1/files/{fid}/download",
            "meta_url": f"{base}/v1/files/{fid}",
        }
    except Exception:
        return {}


def _record_version_safe(
    conv: Conversation,
    kind: str,
    label: str,
    speckle_info: dict,
    blob: Optional[Blob],
) -> None:
    """
    Legt eine Version an. Für DXF genügt blob + kind="BPA_DXF".
    """
    try:
        from versioning import record_version, prune

        meta = {"speckle": speckle_info}
        if blob:
            meta.update(
                {
                    "filename": blob.filename,
                    "mime": blob.mime,
                    "size": blob.size,
                    "sha256": blob.sha256,
                }
            )

        record_version(
            conversation_id=conv.id,
            kind=kind,
            label=label,
            source_message_id=None,
            input_blob_id=blob.id if blob else None,
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
    except Exception:
        current_app.logger.warning("versioning not available or failed", exc_info=True)


def _has_start_card(conv: Conversation) -> bool:
    try:
        for m in list(conv.transcript or []):
            if not isinstance(m, dict):
                continue
            meta = m.get("meta") or {}
            if meta.get("type") == "card" and str(meta.get("template") or "") == "project_welcome":
                return True
        return False
    except Exception:
        return False


def _post_start_card_if_missing(conv: Conversation) -> None:
    try:
        if _has_start_card(conv):
            return
        payload = {
            "wfs_url": current_app.config.get("PROJECT_WELCOME_WFS_URL", ""),
            "layer": current_app.config.get("PROJECT_WELCOME_LAYER", ""),
            "hint": current_app.config.get("PROJECT_WELCOME_HINT", "Der AI-Chat dient zur Vereinfachung der Bedienung unserer tausenden Möglichkeiten, Daten auszuwerten oder Dinge zu erzeugen. Dieser ist noch sehr fehleranfällig, wir arbeiten stetig daran, diesen besser zu machen, denke daran und sei nicht zu hart zu uns ;)"),
        }
        msg.post_card_message(
            conversation=conv,
            template_key="project_welcome",
            payload=payload,
            role="service",
            trace=["system"],
            validate=False,
        )
        try:
            db.session.add(conv)
            db.session.commit()
        except Exception:
            db.session.rollback()
    except Exception:
        current_app.logger.warning("post_start_card failed", exc_info=True)


def _uploads_disabled() -> bool:
    """
    Globale Schalter aus config.py:
      - VIEW_ONLY_MODE     → kompletter Readonly-Betrieb
      - DISABLE_UI_UPLOADS → UI-Uploads gesperrt
    """
    try:
        return bool(current_app.config.get("VIEW_ONLY_MODE")) or bool(
            current_app.config.get("DISABLE_UI_UPLOADS")
        )
    except Exception:
        return True


# ───────────────────────── pages ─────────────────────────

@bp.get("/ui")
def ui_root():
    return redirect(url_for("ui_chat.chat_page"))


@bp.get("/ui/chat")
def chat_page():
    chat_id = request.args.get("chat_id")
    conv = Conversation.query.get(chat_id) if chat_id else None
    if conv is None:
        try:
            conv = Conversation()
            db.session.add(conv)
            db.session.commit()
        except Exception:
            current_app.logger.exception("create conversation failed")
            return redirect(url_for("ui_chat.chat_page"), code=302)
        _post_start_card_if_missing(conv)
        return redirect(url_for("ui_chat.chat_page", chat_id=conv.id), code=302)

    try:
        resp = make_response(render_template("chat.html", chat_id=conv.id))
        _finalize_html_response(resp, no_store=False)
        return resp
    except Exception as ex:
        current_app.logger.exception("render chat.html failed")
        return _simple_html_error(f"chat page render failed: {ex}", 500)


@bp.get("/ui/chat-3d")
def chat_viewer_page():
    chat_id = request.args.get("chat_id")
    conv = Conversation.query.get(chat_id) if chat_id else None
    if conv is None:
        try:
            conv = Conversation()
            db.session.add(conv)
            db.session.commit()
        except Exception:
            current_app.logger.exception("create conversation failed")
            return redirect(url_for("ui_chat.chat_viewer_page"), code=302)
        _post_start_card_if_missing(conv)
        return redirect(url_for("ui_chat.chat_viewer_page", chat_id=conv.id), code=302)

    # Projekt/Modell sicherstellen
    try:
        ensure_and_refresh(conv)
    except Exception:
        current_app.logger.warning("vectoplan ensure/refresh failed", exc_info=True)

    # EMBED-Viewer ermitteln; ggf. Platzhalter erzeugen
    vurl = ""
    try:
        vurl = viewer_url(conv) or ""
        if not vurl and getattr(conv, "vectoplan_project_id", None):
            try:
                ensure_placeholder_if_empty(conv)
                vurl = viewer_url(conv) or ""
            except Exception:
                current_app.logger.warning("ensure_placeholder_if_empty failed", exc_info=True)
    except Exception:
        vurl = ""

    try:
        resp = make_response(render_template("chat_viewer.html", chat_id=conv.id, viewer_url=vurl or ""))
        # Viewer-Seite ist dynamisch (UI/JS) → in DEV no-store, sonst ok
        _finalize_html_response(resp, no_store=False)
        return resp
    except Exception as ex:
        current_app.logger.exception("render chat_viewer.html failed")
        return _simple_html_error(f"viewer page render failed: {ex}", 500)


@bp.get("/ui/chat/<chat_id>/lv")
def lv_page(chat_id: str):
    """
    LV-Tab: rendert templates/viewer/lv.html (als iframe target).
    """
    try:
        conv = Conversation.query.get(chat_id)
        if not conv:
            return _simple_html_error("chat not found", 404)

        resp = make_response(render_template("viewer/lv.html", chat_id=conv.id))
        # Als iframe-content: eher no-store in DEV; prod kann cachen, aber sicher ist: no-store = True
        # Hier bewusst no_store=False, weil Inhalt statisch ist; DEV übernimmt ohnehin no-store.
        _finalize_html_response(resp, no_store=False)
        return resp
    except Exception as ex:
        current_app.logger.exception("render lv.html failed")
        return _simple_html_error(f"lv render failed: {ex}", 500)


@bp.get("/ui/chat/<chat_id>/admin")
def admin_page(chat_id: str):
    """
    Admin-Tab: rendert templates/viewer/admin.html (als iframe target).
    """
    try:
        conv = Conversation.query.get(chat_id)
        if not conv:
            return _simple_html_error("chat not found", 404)

        resp = make_response(render_template("viewer/admin.html", chat_id=conv.id))
        _finalize_html_response(resp, no_store=False)
        return resp
    except Exception as ex:
        current_app.logger.exception("render admin.html failed")
        return _simple_html_error(f"admin render failed: {ex}", 500)


# ───────────────────────── UI helpers (JSON) ─────────────────────────

@bp.get("/ui/chat/<chat_id>/viewer.json")
def viewer_json(chat_id: str):
    """
    Liefert Viewer-URLs *und* die aktuell bekannten IDs (project_id, model_id).
    Das hilft dem Frontend, Query-URLs korrekt aufzubauen.
    """
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404

    try:
        ensure_and_refresh(conv)
    except Exception:
        pass

    vurl = ""
    try:
        vurl = viewer_url(conv) or ""
        if not vurl and getattr(conv, "vectoplan_project_id", None):
            try:
                ensure_placeholder_if_empty(conv)
                vurl = viewer_url(conv) or ""
            except Exception:
                current_app.logger.warning("ensure_placeholder_if_empty failed", exc_info=True)
                vurl = ""
    except Exception:
        vurl = ""

    pid = getattr(conv, "vectoplan_project_id", None)
    mid = getattr(conv, "vectoplan_model_id", None)

    # Optional: aktuelle gespeicherte viewer_selection mitliefern (best effort)
    sel = {}
    try:
        state = ConversationState.get_or_create(conv.id)
        sel = dict(state.state_json.get("viewer_selection") or {})
    except Exception:
        sel = {}

    resp = jsonify({
        "chat_id": conv.id,
        "project_id": pid,
        "model_id": mid,
        "viewer_url": _proxied_viewer_url(vurl),
        "raw_viewer_url": vurl,
        "viewer_selection": sel or None,
    })
    _finalize_json_response(resp, no_store=True)
    return resp, 200


@bp.get("/ui/chat/<chat_id>/versions.json")
def versions_json(chat_id: str):
    conv = Conversation.query.get(chat_id)
    if not conv:
        return jsonify({"error": "not found"}), 404
    try:
        from versioning import list_versions_by_conversation
    except Exception:
        resp = jsonify({"items": [], "total": 0})
        _finalize_json_response(resp, no_store=True)
        return resp, 200

    try:
        kind = request.args.get("kind") or None
        items = list_versions_by_conversation(conversation_id=conv.id, kind=kind) or []
        resp = jsonify({"items": items, "total": len(items)})
        _finalize_json_response(resp, no_store=True)
        return resp, 200
    except Exception as ex:
        current_app.logger.exception("versions_json failed")
        resp = jsonify({"error": str(ex)})
        _finalize_json_response(resp, no_store=True)
        return resp, 500


@bp.get("/ui/templates.json")
def templates_json():
    """Schlanke Liste für das Frontend: key, renderer, title, version."""
    try:
        items = msg.list_templates() or []
        slim = []
        for t in items:
            try:
                slim.append(
                    {
                        "key": t.get("key"),
                        "renderer": t.get("renderer") or "InfoCard",
                        "title": t.get("title") or t.get("key"),
                        "version": int(t.get("version") or 1),
                    }
                )
            except Exception:
                continue
        resp = jsonify({"items": slim, "total": len(slim)})
        _finalize_json_response(resp, no_store=True)
        return resp, 200
    except Exception as ex:
        current_app.logger.exception("templates_json failed")
        resp = jsonify({"error": str(ex)})
        _finalize_json_response(resp, no_store=True)
        return resp, 500


# ───────────────────────── UI upload (multipart) ─────────────────────────

@bp.post("/ui/chat/<chat_id>/upload")
def ui_upload(chat_id: str):
    """
    multipart/form-data
      - 'file'  (einzeln) oder 'files' (mehrere)
      - optional: 'model_name'

    Verhalten:
      - .ifc/.obj/.stl → Upload zu Speckle + Version 'BGA_IFC'/'BGA_MESH'
                         ⮕ Label wird (falls möglich) auf commit_message gesetzt,
                           damit Server- und lokale Liste identisch benannt sind.
      - .dxf           → KEIN Speckle-Upload. Blob speichern + Version 'BPA_DXF'
    """
    # Readonly-Schalter beachten
    if _uploads_disabled():
        payload = {
            "error": "uploads disabled",
            "code": "uploads_disabled",
            "view_only": bool(current_app.config.get("VIEW_ONLY_MODE")),
        }
        resp = jsonify(payload)
        _finalize_json_response(resp, no_store=True)
        return resp, 403

    conv = Conversation.query.get(chat_id)
    if not conv:
        resp = jsonify({"error": "not found"})
        _finalize_json_response(resp, no_store=True)
        return resp, 404

    # Dateien einsammeln
    fs = []
    try:
        if "files" in request.files:
            fs = request.files.getlist("files")
        elif "file" in request.files:
            fs = [request.files["file"]]
    except Exception:
        fs = []
    if not fs:
        resp = jsonify({"error": "no files"})
        _finalize_json_response(resp, no_store=True)
        return resp, 400

    model_name = (request.form.get("model_name") or "").strip() or None
    items: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    # Lazy import für Speckle-Upload
    try:
        from vectoplan import upload_file_to_project
    except Exception as ex:
        upload_file_to_project = None  # type: ignore
        current_app.logger.warning("upload_file_to_project not available: %s", ex)

    # Projekt vorbereiten (nur relevant für 3D)
    try:
        ensure_and_refresh(conv)
    except Exception:
        current_app.logger.warning("ensure_and_refresh in ui_upload failed", exc_info=True)

    for f in fs:
        try:
            ok, ext = _validate_filetype(f.filename, f.mimetype)
            if not ok:
                errors.append({"filename": f.filename, "error": f"unsupported file type: {ext or f.mimetype}"})
                continue

            data = f.read()
            if not data:
                errors.append({"filename": f.filename, "error": "empty file"})
                continue

            # Blob persistieren
            b = Blob(
                filename=f.filename or "file",
                mime=f.mimetype or "application/octet-stream",
                size=len(data),
                sha256="",
                data=data,
            )
            try:
                import hashlib
                b.sha256 = hashlib.sha256(data).hexdigest()
            except Exception:
                b.sha256 = ""
            db.session.add(b)
            db.session.flush()  # b.id verfügbar

            if ext == ".dxf":
                # DXF nicht zu Speckle hochladen. Version anlegen.
                kind = "BPA_DXF"
                try:
                    _record_version_safe(
                        conv=conv,
                        kind=kind,
                        label=os.path.basename(b.filename or "upload.dxf"),
                        speckle_info={},
                        blob=b,
                    )
                except Exception:
                    pass

                # Optionaler direkter DXF-Link
                try:
                    dxf_url = url_for("ui_2dviewer.dxf_blob", chat_id=conv.id, blob_id=b.id)
                except Exception:
                    dxf_url = ""

                items.append(
                    {
                        "file_id": b.id,
                        "filename": b.filename,
                        "mime": b.mime,
                        "size": b.size,
                        "sha256": b.sha256,
                        "ext": ext,
                        "kind": kind,
                        "dxf_url": dxf_url,
                        **_file_urls(b.id),
                    }
                )

            else:
                # 3D-Uploads (IFC/OBJ/STL)
                if not upload_file_to_project:
                    errors.append({"filename": f.filename, "error": "3D upload not available on server"})
                    continue

                info = upload_file_to_project(conv=conv, blob=b, model_name=model_name, file_ext=ext) or {}
                vurl = info.get("viewer_url") or ""
                # Bevorzugt commit_message für die lokale Version übernehmen (Namensgleichheit)
                label = (info.get("commit_message") or os.path.basename(b.filename or "upload")).strip() or "upload"
                kind = "BGA_IFC" if ext == ".ifc" else "BGA_MESH"

                try:
                    _record_version_safe(
                        conv=conv,
                        kind=kind,
                        label=label,
                        speckle_info=info,
                        blob=b,
                    )
                except Exception:
                    pass

                # Nach erfolgreichem Upload die Viewer-Selektion auf *diese* Version setzen
                try:
                    if info.get("project_id") and info.get("model_id") and info.get("version_id"):
                        ConversationState.merge_patch(conv.id, {
                            "viewer_selection": {
                                "mode": "version",
                                "project_id": info.get("project_id"),
                                "model_id": info.get("model_id"),
                                "version_id": info.get("version_id"),
                            }
                        })
                except Exception:
                    current_app.logger.warning("ui_upload: save viewer_selection failed", exc_info=True)

                items.append(
                    {
                        "file_id": b.id,
                        "filename": b.filename,
                        "mime": b.mime,
                        "size": b.size,
                        "sha256": b.sha256,
                        "ext": ext,
                        "kind": kind,
                        "project_id": info.get("project_id"),
                        "model_id": info.get("model_id"),
                        "version_id": info.get("version_id"),
                        "viewer_url": _proxied_viewer_url(vurl),
                        **_file_urls(b.id),
                    }
                )

        except Exception as ex:
            current_app.logger.exception("ui_upload failed for %s", getattr(f, "filename", ""))
            errors.append({"filename": getattr(f, "filename", ""), "error": str(ex)})

    # Änderungen sichern
    try:
        db.session.add(conv)
        db.session.commit()
    except Exception:
        current_app.logger.warning("DB commit after ui_upload failed", exc_info=True)

    # Einheitliche, frontendsichere Antwort
    status = 201 if items and not errors else (207 if items and errors else 422)
    body = {
        "status": "ok" if items else "error",
        "chat_id": conv.id,
        "items": items,
        "results": items,
        "errors": errors,
        "total": len(items),
    }
    resp = jsonify(body)
    _finalize_json_response(resp, no_store=True)
    return resp, status