# /services/app/routes/chat/helpers.py
from __future__ import annotations

import base64
import json
import os
import time
from typing import Iterable, List, Dict, Optional, Tuple, Any

import requests
from flask import current_app
from extensions import db
from models import Conversation, Blob

# Template-/State-Tools
import messages as msg  # servereigene Nachrichten-/State-Helfer

# VectoPlan (Kompatibilitätsshift re-exportiert das neue Paket)
from vectoplan import (
    ensure_and_refresh,
    viewer_url,
    ensure_placeholder_if_empty,
)

# ───────────────────────── config / constants ─────────────────────────

ALLOWED_EXTS = {".ifc", ".obj", ".stl"}
ALLOWED_MIMES = {
    "model/ifc", "application/octet-stream",
    "model/obj", "text/plain",
    "model/stl", "application/sla", "model/x.stl-binary",
}
DEFAULT_KEEP_VERSIONS = 10
AUTO_UPLOAD = True  # kann via Config überschrieben werden


# ───────────────────────── helpers (generic) ─────────────────────────

def cfg_int(key: str, default: int) -> int:
    try:
        v = int(current_app.config.get(key, default))
        return v if v >= 0 else default
    except Exception:
        return default


def b64(data: bytes) -> str:
    try:
        return base64.b64encode(data).decode("ascii")
    except Exception:
        return ""


def make_file_urls(file_id: str) -> Dict[str, str]:
    """
    Liefert relative URLs für Content/Download/Meta.
    """
    try:
        from urllib.parse import quote as _q
        fid = _q(str(file_id or ""), safe="")
        base = ""  # relative Pfade
        return {
            "content_url": f"{base}/v1/files/{fid}/content",
            "download_url": f"{base}/v1/files/{fid}/download",
            "meta_url": f"{base}/v1/files/{fid}",
        }
    except Exception:
        return {}


def attachment_meta_for_transcript(b: Blob) -> Dict[str, Any]:
    """
    Einheitliches Attachment-Objekt für das Transcript.
    Enthält:
      - id/file_id, filename, mime, size
      - content_url/download_url/meta_url (für Chips/Preview)
      - optional base64 (nur wenn ATTACHMENT_INLINE_BASE64_MAX > 0 und Größe ok)
    """
    try:
        meta = {
            "id": b.id,
            "file_id": b.id,               # Kompatibilität
            "filename": b.filename,
            "name": b.filename,            # Kompatibilität
            "mime": b.mime,
            "size": b.size,
            **make_file_urls(b.id),
        }
        if should_inline_b64(b.size or 0, b.mime or ""):
            try:
                meta["base64"] = b64(b.data)
            except Exception:
                pass
        return meta
    except Exception:
        return {"id": getattr(b, "id", None)}


def should_inline_b64(size: int, mime: str) -> bool:
    try:
        limit = cfg_int("ATTACHMENT_INLINE_BASE64_MAX", 0)
        if limit <= 0 or size > limit:
            return False
        m = (mime or "").lower()
        return m.startswith("image/") or m.startswith("text/") or m in {"application/json", "application/xml"}
    except Exception:
        return False


def load_attachments(file_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Für ChatAI: liefert Metadaten mit optionalem base64 und URLs.
    """
    out: List[Dict[str, Any]] = []
    for fid in file_ids or []:
        try:
            b = Blob.query.get(str(fid))
        except Exception:
            b = None
        if not b:
            continue
        try:
            item = {
                "id": b.id,
                "filename": b.filename,
                "mime": b.mime,
                "size": b.size,
                "sha256": b.sha256,
                **make_file_urls(b.id),
            }
            if should_inline_b64(b.size or 0, b.mime or ""):
                item["base64"] = b64(b.data)
            out.append(item)
        except Exception:
            continue
    return out


def chunk_text(text: str, size: int = 18) -> Iterable[str]:
    words = (text or "").split()
    buf: List[str] = []
    for w in words:
        buf.append(w)
        if len(buf) >= size:
            yield " ".join(buf) + " "
            buf = []
    if buf:
        yield " ".join(buf)


def sse(obj: dict) -> str:
    try:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
    except Exception:
        return "data: {}\n\n"


def chatai_url() -> str:
    try:
        return (current_app.config.get("CHATAI_URL") or "http://chatai:8001/chat").strip()
    except Exception:
        return "http://chatai:8001/chat"


def numeric_project_id(c: Conversation) -> int:
    try:
        pid = getattr(c, "project_id", None)
        if isinstance(pid, int) and pid > 0:
            return pid
        if isinstance(pid, str) and pid.isdigit():
            v = int(pid)
            return v if v > 0 else 1
        sid = str(getattr(c, "id", "") or "")
        try:
            return max(1, int(sid[:8], 16))
        except Exception:
            s = sum(ord(ch) for ch in sid) or 1
            return s
    except Exception:
        return 1


def resolve_viewer_url(c: Conversation) -> str:
    """
    Einheitlich: versuche viewer_url(c).
    Wenn leer, lege Placeholder an und frage erneut viewer_url(c) ab.
    """
    try:
        v = viewer_url(c) or ""
        if v:
            return v
        if getattr(c, "vectoplan_project_id", None):
            try:
                ensure_placeholder_if_empty(c)
                v2 = viewer_url(c) or ""
                return v2 or ""
            except Exception:
                current_app.logger.warning("ensure_placeholder_if_empty failed", exc_info=True)
                return ""
        return ""
    except Exception:
        return ""


def ext_of(filename: str) -> str:
    try:
        return os.path.splitext(filename or "")[1].lower()
    except Exception:
        return ""


def validate_filetype(blob: Blob) -> Tuple[bool, str]:
    try:
        ext = ext_of(blob.filename)
        if ext in ALLOWED_EXTS:
            return True, ext
        mime = (blob.mime or "").lower()
        if mime in ALLOWED_MIMES:
            if "ifc" in mime:
                return True, ".ifc"
            if "stl" in mime or "sla" in mime:
                return True, ".stl"
            if "obj" in mime:
                return True, ".obj"
        return False, ext
    except Exception:
        return False, ""


def extract_reply_text(out: object) -> str:
    """
    Robust gegen dict | list | str | bytes.
    Erwartete Felder: text | reply | message | choices[].text/content
    """
    try:
        if out is None:
            return ""
        if isinstance(out, str):
            return out
        if isinstance(out, bytes):
            try:
                return out.decode("utf-8", errors="ignore")
            except Exception:
                return ""
        if isinstance(out, dict):
            for k in ("text", "reply", "message", "content"):
                v = out.get(k)
                if isinstance(v, (str, bytes)):
                    return v if isinstance(v, str) else v.decode("utf-8", errors="ignore")
            ch = out.get("choices")
            if isinstance(ch, list) and ch:
                first = ch[0] or {}
                if isinstance(first, dict):
                    if isinstance(first.get("text"), str):
                        return first["text"]
                    msg_ = first.get("message") or {}
                    if isinstance(msg_, dict):
                        if isinstance(msg_.get("content"), str):
                            return msg_["content"]
            return json.dumps(out, ensure_ascii=False)
        if isinstance(out, list):
            if all(isinstance(x, str) for x in out):
                return "\n".join(out)
            parts: List[str] = []
            for x in out:
                if isinstance(x, dict):
                    for k in ("text", "content", "reply", "message"):
                        v = x.get(k)
                        if isinstance(v, str):
                            parts.append(v)
                            break
                elif isinstance(x, (str, bytes)):
                    parts.append(x if isinstance(x, str) else x.decode("utf-8", errors="ignore"))
            return "\n".join([p for p in parts if p]) if parts else str(out)
        return str(out)
    except Exception:
        return ""


def record_version_safe(conv: Conversation,
                        kind: str,
                        label: str,
                        source_message_id: Optional[str],
                        speckle_info: Dict[str, str],
                        input_blob: Optional[Blob]) -> Dict[str, Any]:
    try:
        from versioning import record_version, prune
        meta = {"speckle": speckle_info}
        if input_blob:
            meta.update({
                "filename": input_blob.filename,
                "mime": input_blob.mime,
                "size": input_blob.size,
                "sha256": input_blob.sha256,
            })
        row = record_version(
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
        keep = int(current_app.config.get("KEEP_VERSIONS_PER_PROJECT", DEFAULT_KEEP_VERSIONS)) or DEFAULT_KEEP_VERSIONS
        try:
            prune(conversation_id=conv.id, kind=kind, keep=keep)
        except Exception:
            current_app.logger.warning("version prune failed", exc_info=True)
        return row or {}
    except Exception:
        current_app.logger.warning("versioning module not available or failed", exc_info=True)
    return {}


def auto_upload_supported_attachments(conv: Conversation,
                                      file_ids: List[str],
                                      source_message_id: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Lädt unterstützte Dateien (.ifc/.obj/.stl) in Speckle und legt Versionen an.
    Liefert Liste erfolgreicher Uploads [{file_id, ext, project_id, model_id, version_id, viewer_url}].
    """
    results: List[Dict[str, str]] = []
    if not (file_ids and (current_app.config.get("AUTO_UPLOAD_ATTACHMENTS", AUTO_UPLOAD) is True)):
        return results

    try:
        from vectoplan import upload_file_to_project  # lazy
    except Exception as ex:
        current_app.logger.warning("upload_file_to_project not available: %s", ex)
        return results

    for fid in file_ids:
        try:
            b = Blob.query.get(str(fid))
            if not b:
                continue
            ok, ext = validate_filetype(b)
            if not ok:
                continue

            try:
                ensure_and_refresh(conv)
            except Exception:
                current_app.logger.warning("ensure_and_refresh before upload failed", exc_info=True)

            info = upload_file_to_project(conv=conv, blob=b, model_name=None, file_ext=ext) or {}
            vurl = info.get("viewer_url") or ""
            kind = "BGA_IFC" if ext == ".ifc" else "BGA_MESH"
            try:
                record_version_safe(
                    conv=conv,
                    kind=kind,
                    label=os.path.basename(b.filename or "upload"),
                    source_message_id=source_message_id,
                    speckle_info=info,
                    input_blob=b,
                )
            except Exception:
                pass

            results.append({
                "file_id": b.id,
                "ext": ext,
                "project_id": info.get("project_id"),
                "model_id": info.get("model_id"),
                "version_id": info.get("version_id"),
                "viewer_url": vurl,
            })
        except Exception as ex:
            current_app.logger.warning("auto upload failed for %s: %s", fid, ex, exc_info=True)

    try:
        db.session.add(conv)
        db.session.commit()
    except Exception:
        current_app.logger.warning("DB commit after auto-upload failed", exc_info=True)

    return results


# ───────────────────────── ChatAI Integration ─────────────────────────

def call_chatai(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Ruft ChatAI und gibt (parsed_json, reply_text) zurück.
    reply_text ist immer gesetzt (kann leer sein), parsed_json nur wenn Dict.
    """
    try:
        r = requests.post(chatai_url(), json=payload, timeout=300)
        r.raise_for_status()
        out = r.json()
        txt = extract_reply_text(out)
        return (out if isinstance(out, dict) else None), txt
    except Exception as ex:
        current_app.logger.warning("ChatAI call failed: %s", ex, exc_info=True)
        return None, f"Fehler in ChatAI: {ex}"


def post_missing_slots_card(conv: Conversation, missing: List[str]) -> None:
    try:
        payload = {"missing": list(missing or []), "tips": []}
        msg.post_card_message(conversation=conv, template_key="missing_slots", payload=payload, role="service", trace=["ChatAI"], validate=False)
    except Exception:
        pass


def maybe_post_viewer_card(conv: Conversation, info: Dict[str, Any]) -> None:
    try:
        vurl = info.get("viewer_url") or resolve_viewer_url(conv)
        if vurl:
            msg.post_card_message(conversation=conv, template_key="speckle_viewer", payload={"url": vurl, "caption": "3D-Ansicht"}, role="service", trace=["BGA"], validate=False)
    except Exception:
        pass


def apply_chatai_actions(conv: Conversation, out_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Führt Aktionen aus. Behandelt need_info/Missing Slots.
    Verhindert doppelte 'missing_slots'-Karten, wenn ChatAI sie bereits als Action gepostet hat.
    """
    results: Dict[str, Any] = {"actions": [], "status": None, "intent": None}
    try:
        status = str(out_json.get("status") or "").lower() if isinstance(out_json, dict) else ""
        intent = out_json.get("intent") if isinstance(out_json, dict) else None
        missing = out_json.get("missing") if isinstance(out_json, dict) else None
        actions = out_json.get("actions") if isinstance(out_json, dict) else None

        results["status"] = status or None
        results["intent"] = intent

        if isinstance(actions, list) and actions:
            try:
                exec_res = msg.run_actions(conversation=conv, actions=actions)
                results["actions"] = exec_res.get("results") or []
            except Exception as ex:
                current_app.logger.warning("run_actions failed: %s", ex, exc_info=True)

        posted_missing = False
        try:
            for a in (actions or []):
                if isinstance(a, dict) and str(a.get("type") or "").lower() == "post_message":
                    if str(a.get("template") or "").strip() == "missing_slots":
                        posted_missing = True
                        break
        except Exception:
            posted_missing = False

        if ((status == "need_info") or (isinstance(missing, list) and missing)) and not posted_missing:
            post_missing_slots_card(conv, missing if isinstance(missing, list) else [])
    except Exception as ex:
        current_app.logger.warning("apply_chatai_actions failed: %s", ex, exc_info=True)
    return results
