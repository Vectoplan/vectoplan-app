# services/app/routes/chat/helpers.py
from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import quote

import requests
from flask import current_app

from extensions import db
from models import Blob, Conversation

# Server-owned message/template/action helpers.
import messages as msg


# ───────────────────────── Constants ─────────────────────────

DEFAULT_KEEP_VERSIONS = 10
DEFAULT_CHATAI_TIMEOUT = 300

# Automatic legacy 3D publishing is intentionally disabled.
# The function name `auto_upload_supported_attachments` is kept only for
# compatibility with sync.py / stream.py until those files are cleaned.
AUTO_UPLOAD = False

SUPPORTED_ARTIFACT_EXTS = {
    ".ifc",
    ".obj",
    ".stl",
    ".gltf",
    ".glb",
    ".dxf",
}

SUPPORTED_ARTIFACT_MIMES = {
    # Generic / fallback
    "application/octet-stream",
    "text/plain",
    # IFC
    "model/ifc",
    "application/ifc",
    "application/x-ifc",
    # OBJ
    "model/obj",
    # STL
    "model/stl",
    "application/sla",
    "model/x.stl-binary",
    # GLTF / GLB
    "model/gltf+json",
    "model/gltf-binary",
    "application/gltf+json",
    "application/gltf-buffer",
    # DXF
    "application/dxf",
    "application/x-dxf",
    "image/vnd.dxf",
    "image/x-dxf",
}

INLINE_MIMES = {
    "application/json",
    "application/xml",
    "text/json",
}

LEGACY_TEMPLATE_KEYS = {
    "spe" + "ckle_viewer",
}

LEGACY_RENDERERS = {
    "Spe" + "ckleViewerCard",
}

LEGACY_DROP_KEYS = {
    "spe" + "ckle",
    "spe" + "ckle_project_id",
    "spe" + "ckle_model_id",
    "spe" + "ckle_version_id",
    "viewer_url",
    "raw_viewer_url",
    "old_viewer_url",
    "project_id",
    "model_id",
    "version_id",
    "commit_id",
}


# ───────────────────────── Generic config helpers ─────────────────────────

def cfg_int(key: str, default: int) -> int:
    try:
        value = int(current_app.config.get(key, default))
        return value if value >= 0 else default
    except Exception:
        return default


def cfg_bool(key: str, default: bool = False) -> bool:
    try:
        value = current_app.config.get(key, default)

        if isinstance(value, bool):
            return value

        text = str(value).strip().lower()

        if text in {"1", "true", "yes", "y", "on"}:
            return True

        if text in {"0", "false", "no", "n", "off"}:
            return False

        return default

    except Exception:
        return default


def cfg_str(key: str, default: str = "") -> str:
    try:
        value = current_app.config.get(key, default)
        return str(value if value is not None else default).strip()
    except Exception:
        return default


# ───────────────────────── File / attachment helpers ─────────────────────────

def b64(data: bytes) -> str:
    try:
        return base64.b64encode(data or b"").decode("ascii")
    except Exception:
        return ""


def make_file_urls(file_id: str) -> Dict[str, str]:
    """
    Return relative file URLs for transcript chips and frontend previews.
    """
    try:
        safe_id = quote(str(file_id or ""), safe="")

        return {
            "content_url": f"/v1/files/{safe_id}/content",
            "download_url": f"/v1/files/{safe_id}/download",
            "meta_url": f"/v1/files/{safe_id}",
        }

    except Exception:
        return {}


def should_inline_b64(size: int, mime: str) -> bool:
    try:
        limit = cfg_int("ATTACHMENT_INLINE_BASE64_MAX", 0)

        if limit <= 0:
            return False

        if int(size or 0) > limit:
            return False

        normalized_mime = str(mime or "").strip().lower()

        return (
            normalized_mime.startswith("image/")
            or normalized_mime.startswith("text/")
            or normalized_mime in INLINE_MIMES
        )

    except Exception:
        return False


def attachment_meta_for_transcript(blob: Blob) -> Dict[str, Any]:
    """
    Return a stable attachment object for Conversation.transcript.
    """
    try:
        meta: Dict[str, Any] = {
            "id": blob.id,
            "file_id": blob.id,
            "filename": blob.filename,
            "name": blob.filename,
            "mime": blob.mime,
            "size": blob.size,
            "sha256": getattr(blob, "sha256", ""),
            **make_file_urls(blob.id),
        }

        if should_inline_b64(blob.size or 0, blob.mime or ""):
            try:
                meta["base64"] = b64(blob.data)
            except Exception:
                pass

        return meta

    except Exception:
        return {"id": getattr(blob, "id", None)}


def _get_blob(file_id: str) -> Optional[Blob]:
    try:
        return db.session.get(Blob, str(file_id))
    except Exception:
        try:
            return Blob.query.get(str(file_id))
        except Exception:
            return None


def load_attachments(file_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Load attachment metadata for ChatAI.

    This does not upload or publish files anywhere.
    """
    out: List[Dict[str, Any]] = []

    for file_id in file_ids or []:
        blob = _get_blob(str(file_id))

        if not blob:
            continue

        try:
            item: Dict[str, Any] = {
                "id": blob.id,
                "file_id": blob.id,
                "filename": blob.filename,
                "name": blob.filename,
                "mime": blob.mime,
                "size": blob.size,
                "sha256": blob.sha256,
                **make_file_urls(blob.id),
            }

            if should_inline_b64(blob.size or 0, blob.mime or ""):
                item["base64"] = b64(blob.data)

            out.append(item)

        except Exception:
            continue

    return out


def ext_of(filename: str) -> str:
    try:
        return os.path.splitext(filename or "")[1].lower()
    except Exception:
        return ""


def validate_filetype(blob: Blob) -> Tuple[bool, str]:
    """
    Validate known artifact file types.

    This helper only validates. It does not upload, publish or import.
    """
    try:
        ext = ext_of(getattr(blob, "filename", "") or "")

        if ext in SUPPORTED_ARTIFACT_EXTS:
            return True, ext

        mime = str(getattr(blob, "mime", "") or "").strip().lower()

        if mime in SUPPORTED_ARTIFACT_MIMES:
            if "ifc" in mime:
                return True, ".ifc"
            if "stl" in mime or "sla" in mime:
                return True, ".stl"
            if "obj" in mime or mime == "text/plain":
                return True, ".obj"
            if "gltf+json" in mime:
                return True, ".gltf"
            if "gltf-binary" in mime or "gltf-buffer" in mime:
                return True, ".glb"
            if "dxf" in mime or "vnd.dxf" in mime or "x-dxf" in mime:
                return True, ".dxf"

        return False, ext

    except Exception:
        return False, ""


def kind_for_ext(ext: str) -> str:
    if ext == ".dxf":
        return "BPA_DXF"
    if ext == ".ifc":
        return "BGA_IFC"
    if ext in {".obj", ".stl", ".gltf", ".glb"}:
        return "BGA_MESH"
    return "FILE"


# ───────────────────────── Text / SSE helpers ─────────────────────────

def chunk_text(text: str, size: int = 18) -> Iterable[str]:
    words = str(text or "").split()
    buffer: List[str] = []

    for word in words:
        buffer.append(word)

        if len(buffer) >= size:
            yield " ".join(buffer) + " "
            buffer = []

    if buffer:
        yield " ".join(buffer)


def sse(obj: dict) -> str:
    try:
        return f"data: {json.dumps(obj or {}, ensure_ascii=False)}\n\n"
    except Exception:
        return "data: {}\n\n"


def extract_reply_text(out: object) -> str:
    """
    Extract reply text robustly from dict | list | str | bytes.
    """
    try:
        if out is None:
            return ""

        if isinstance(out, str):
            return out

        if isinstance(out, bytes):
            return out.decode("utf-8", errors="ignore")

        if isinstance(out, dict):
            for key in ("text", "reply", "message", "content"):
                value = out.get(key)

                if isinstance(value, str):
                    return value

                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="ignore")

            choices = out.get("choices")

            if isinstance(choices, list) and choices:
                first = choices[0] or {}

                if isinstance(first, dict):
                    if isinstance(first.get("text"), str):
                        return first["text"]

                    nested_message = first.get("message") or {}

                    if isinstance(nested_message, dict) and isinstance(
                        nested_message.get("content"),
                        str,
                    ):
                        return nested_message["content"]

            return json.dumps(out, ensure_ascii=False)

        if isinstance(out, list):
            if all(isinstance(item, str) for item in out):
                return "\n".join(out)

            parts: List[str] = []

            for item in out:
                if isinstance(item, dict):
                    for key in ("text", "content", "reply", "message"):
                        value = item.get(key)
                        if isinstance(value, str):
                            parts.append(value)
                            break

                elif isinstance(item, bytes):
                    parts.append(item.decode("utf-8", errors="ignore"))

                elif isinstance(item, str):
                    parts.append(item)

            return "\n".join([part for part in parts if part]) if parts else str(out)

        return str(out)

    except Exception:
        return ""


# ───────────────────────── Chat / editor compatibility helpers ─────────────────────────

def chatai_url() -> str:
    try:
        return cfg_str("CHATAI_URL", "http://chatai:8001/chat")
    except Exception:
        return "http://chatai:8001/chat"


def numeric_project_id(conversation: Conversation) -> int:
    """
    Compatibility helper for ChatAI payloads that expect a numeric project id.

    This does not create or resolve any app/editor project structure.
    """
    try:
        project_id = getattr(conversation, "project_id", None)

        if isinstance(project_id, int) and project_id > 0:
            return project_id

        if isinstance(project_id, str) and project_id.isdigit():
            value = int(project_id)
            return value if value > 0 else 1

        sid = str(getattr(conversation, "id", "") or "")

        try:
            return max(1, int(sid[:8], 16))
        except Exception:
            total = sum(ord(char) for char in sid) or 1
            return total

    except Exception:
        return 1


def resolve_viewer_url(conversation: Conversation) -> str:
    """
    Compatibility name for old callers.

    Returns the local editor iframe route. It does not call an old viewer,
    create placeholders or contact any 3D backend.
    """
    try:
        chat_id = quote(str(getattr(conversation, "id", "") or ""), safe="")

        if not chat_id:
            return ""

        return f"/ui/chat/{chat_id}/editor"

    except Exception:
        return ""


# ───────────────────────── Metadata sanitizing ─────────────────────────

def _is_legacy_key(key: Any) -> bool:
    try:
        text = str(key or "").strip().lower()

        if not text:
            return False

        if text in LEGACY_DROP_KEYS:
            return True

        if text.startswith("spe" + "ckle"):
            return True

        return False

    except Exception:
        return False


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return None

    try:
        if isinstance(value, Mapping):
            clean: Dict[str, Any] = {}

            for key, item in value.items():
                if _is_legacy_key(key):
                    continue

                key_text = str(key or "").strip()
                if not key_text:
                    continue

                clean[key_text[:160]] = _json_safe(item, depth=depth + 1)

            return clean

        if isinstance(value, list):
            return [_json_safe(item, depth=depth + 1) for item in value]

        if isinstance(value, tuple):
            return [_json_safe(item, depth=depth + 1) for item in value]

        if isinstance(value, (str, int, float, bool)) or value is None:
            return value

        return str(value)

    except Exception:
        return None


def _sanitize_actions(actions: Any) -> List[Dict[str, Any]]:
    if not isinstance(actions, list):
        return []

    sanitized: List[Dict[str, Any]] = []

    for action in actions:
        if not isinstance(action, dict):
            continue

        try:
            template_key = str(action.get("template") or action.get("template_key") or "").strip()
            renderer = str(action.get("renderer") or "").strip()

            if template_key in LEGACY_TEMPLATE_KEYS:
                continue

            if renderer in LEGACY_RENDERERS:
                continue

            clean_action = _json_safe(action)

            if isinstance(clean_action, dict):
                sanitized.append(clean_action)

        except Exception:
            continue

    return sanitized


# ───────────────────────── Version helpers ─────────────────────────

def record_version_safe(
    conv: Conversation,
    kind: str,
    label: str,
    source_message_id: Optional[str] = None,
    legacy_info: Optional[Dict[str, Any]] = None,
    input_blob: Optional[Blob] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Record a neutral local version.

    Backward-compatible signature:
    - old callers may still pass a fifth positional legacy info dict
    - old callers may still pass unknown keyword arguments
    - no old backend identifiers are persisted
    """
    try:
        from versioning import prune, record_version

        if input_blob is None and isinstance(kwargs.get("input_blob"), Blob):
            input_blob = kwargs.get("input_blob")

        clean_meta: Dict[str, Any] = {
            "source": "chat.helpers",
            "legacy_3d_backend": False,
        }

        if legacy_info:
            sanitized_legacy = _json_safe(legacy_info)
            if isinstance(sanitized_legacy, dict) and sanitized_legacy:
                clean_meta["legacy_payload_sanitized"] = sanitized_legacy

        extra_meta = kwargs.get("meta")
        if isinstance(extra_meta, dict):
            sanitized_extra = _json_safe(extra_meta)
            if isinstance(sanitized_extra, dict):
                clean_meta.update(sanitized_extra)

        if input_blob:
            clean_meta.update(
                {
                    "filename": input_blob.filename,
                    "mime": input_blob.mime,
                    "size": input_blob.size,
                    "sha256": input_blob.sha256,
                    **make_file_urls(input_blob.id),
                }
            )

        row = record_version(
            conversation_id=conv.id,
            kind=str(kind or "FILE").strip() or "FILE",
            label=str(label or kind or "Version").strip() or "Version",
            source_message_id=source_message_id,
            input_blob_id=input_blob.id if input_blob else None,
            status="stored",
            meta=clean_meta,
            source_service="vectoplan-app",
            source_kind="chat-attachment",
            editor_url=resolve_viewer_url(conv),
        )

        keep = (
            int(current_app.config.get("KEEP_VERSIONS_PER_PROJECT", DEFAULT_KEEP_VERSIONS))
            or DEFAULT_KEEP_VERSIONS
        )

        try:
            prune(conversation_id=conv.id, kind=kind, keep=keep)
        except Exception:
            current_app.logger.warning("version prune failed", exc_info=True)

        return row or {}

    except Exception:
        current_app.logger.warning("versioning module not available or failed", exc_info=True)
        return {}


def auto_upload_supported_attachments(
    conv: Conversation,
    file_ids: List[str],
    source_message_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Compatibility function for sync.py / stream.py.

    It no longer uploads or publishes anything. If explicitly enabled through
    AUTO_UPLOAD_ATTACHMENTS, it only records local neutral versions for known
    artifact files. The default is disabled.
    """
    results: List[Dict[str, Any]] = []

    try:
        enabled = cfg_bool("AUTO_UPLOAD_ATTACHMENTS", AUTO_UPLOAD)

        if not enabled:
            return results

        for file_id in file_ids or []:
            try:
                blob = _get_blob(str(file_id))

                if not blob:
                    continue

                ok, ext = validate_filetype(blob)

                if not ok:
                    continue

                kind = kind_for_ext(ext)

                record_version_safe(
                    conv=conv,
                    kind=kind,
                    label=os.path.basename(blob.filename or "upload"),
                    source_message_id=source_message_id,
                    input_blob=blob,
                    meta={
                        "stored_only": True,
                        "ext": ext,
                    },
                )

                results.append(
                    {
                        "file_id": blob.id,
                        "ext": ext,
                        "kind": kind,
                        "stored_only": True,
                        "editor_url": resolve_viewer_url(conv),
                        "legacy_3d_backend": False,
                    }
                )

            except Exception as exc:
                current_app.logger.warning(
                    "local attachment versioning failed for %s: %s",
                    file_id,
                    exc,
                    exc_info=True,
                )

        try:
            db.session.add(conv)
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.warning("DB commit after attachment versioning failed", exc_info=True)

        return results

    except Exception:
        current_app.logger.warning("auto_upload_supported_attachments compatibility failed", exc_info=True)
        return []


# ───────────────────────── ChatAI integration ─────────────────────────

def call_chatai(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Call ChatAI and return (parsed_json, reply_text).

    reply_text is always a string. parsed_json is only set for dict responses.
    """
    try:
        timeout = cfg_int("CHATAI_TIMEOUT", DEFAULT_CHATAI_TIMEOUT)

        response = requests.post(
            chatai_url(),
            json=payload,
            timeout=timeout,
        )

        response.raise_for_status()

        try:
            output = response.json()
        except Exception:
            output = response.text

        reply_text = extract_reply_text(output)

        return (output if isinstance(output, dict) else None), reply_text

    except Exception as exc:
        current_app.logger.warning("ChatAI call failed: %s", exc, exc_info=True)
        return None, f"Fehler in ChatAI: {exc}"


def post_missing_slots_card(conv: Conversation, missing: List[str]) -> None:
    try:
        payload = {
            "missing": list(missing or []),
            "tips": [],
        }

        msg.post_card_message(
            conversation=conv,
            template_key="missing_slots",
            payload=payload,
            role="service",
            trace=["ChatAI"],
            validate=False,
        )

    except Exception:
        pass


def maybe_post_viewer_card(conv: Conversation, info: Dict[str, Any]) -> None:
    """
    Compatibility no-op.

    The editor is a fixed iframe in chat_viewer.html. Chat messages should not
    post old 3D viewer cards anymore.
    """
    try:
        return None
    except Exception:
        return None


def apply_chatai_actions(conv: Conversation, out_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute ChatAI actions defensively.

    Old viewer-card actions are filtered out before calling the message action
    runner. Missing-slot cards are still supported.
    """
    results: Dict[str, Any] = {
        "actions": [],
        "status": None,
        "intent": None,
    }

    try:
        if not isinstance(out_json, dict):
            return results

        status = str(out_json.get("status") or "").lower()
        intent = out_json.get("intent")
        missing = out_json.get("missing")
        actions = out_json.get("actions")

        results["status"] = status or None
        results["intent"] = intent

        sanitized_actions = _sanitize_actions(actions)

        if sanitized_actions:
            try:
                exec_result = msg.run_actions(
                    conversation=conv,
                    actions=sanitized_actions,
                )
                results["actions"] = exec_result.get("results") or []
            except Exception as exc:
                current_app.logger.warning("run_actions failed: %s", exc, exc_info=True)

        posted_missing = False

        try:
            for action in sanitized_actions:
                if not isinstance(action, dict):
                    continue

                action_type = str(action.get("type") or "").lower()
                template_key = str(action.get("template") or action.get("template_key") or "").strip()

                if action_type == "post_message" and template_key == "missing_slots":
                    posted_missing = True
                    break

        except Exception:
            posted_missing = False

        if (
            (status == "need_info" or (isinstance(missing, list) and missing))
            and not posted_missing
        ):
            post_missing_slots_card(
                conv,
                missing if isinstance(missing, list) else [],
            )

    except Exception as exc:
        current_app.logger.warning("apply_chatai_actions failed: %s", exc, exc_info=True)

    return results