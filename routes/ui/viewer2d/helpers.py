# services/app/routes/ui/viewer2d/helpers.py
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from flask import abort, current_app, request

from extensions import db
from models import Blob, Conversation


# ───────────────────────── Constants ─────────────────────────

_DXF_KINDS = {
    "BPA_DXF",
    "BPA_PLAN_DXF",
    "BPA_PLAN",
    "DXF_UPLOAD",
    "PLAN_DXF",
    "CAD_DXF",
}

_DXF_MIMES = {
    "application/dxf",
    "application/x-dxf",
    "image/vnd.dxf",
    "image/x-dxf",
    "text/plain",
    "application/octet-stream",
}

_MAX_CAD_FILENAME_LENGTH = 180
_DEFAULT_CAD_INTERNAL_BASE = "http://cad:8050"
_DEFAULT_CAD_PUBLIC_BASE = "http://localhost:8050"


# ───────────────────────── Generic helpers ─────────────────────────

def _clean_text(value: Any, default: str = "", max_len: int = 240) -> str:
    try:
        text = str(value if value is not None else default).strip()

        if not text:
            text = default

        if max_len > 0 and len(text) > max_len:
            return text[:max_len]

        return text

    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else default
    except Exception:
        return default


def _ext_of(filename: str) -> str:
    try:
        return os.path.splitext(filename or "")[1].lower()
    except Exception:
        return ""


def _is_dxf_name(name: str | None) -> bool:
    try:
        return _ext_of(name or "") == ".dxf"
    except Exception:
        return False


def _looks_like_dxf_mime(mime: Any) -> bool:
    try:
        normalized = str(mime or "").strip().lower()

        if not normalized:
            return False

        if normalized in _DXF_MIMES:
            return True

        return "dxf" in normalized

    except Exception:
        return False


def _is_local_path(url_or_path: str | None) -> bool:
    """
    Only same-origin relative paths are allowed.

    Allowed:
      /v1/files/abc/content
      /ui/chat/abc/file/123.dxf

    Rejected:
      //example.com/x
      http://example.com/x
      https://example.com/x
      javascript:...
    """
    try:
        value = str(url_or_path or "").strip()

        if not value:
            return False

        if value.startswith("//"):
            return False

        lowered = value.lower()

        if lowered.startswith("http://") or lowered.startswith("https://"):
            return False

        if ":" in value.split("/", 1)[0]:
            return False

        return value.startswith("/")

    except Exception:
        return False


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return None

    try:
        if isinstance(value, Mapping):
            clean: Dict[str, Any] = {}

            for key, item in value.items():
                key_text = _clean_text(key, "", 160)

                if not key_text:
                    continue

                # Do not carry old 3D backend metadata into the 2D/CAD layer.
                lowered = key_text.lower()
                if lowered.startswith("spe" + "ckle"):
                    continue
                if lowered in {
                    "model_id",
                    "commit_id",
                    "stream_id",
                    "viewer_url",
                    "raw_viewer_url",
                    "old_viewer_url",
                }:
                    continue

                clean[key_text] = _json_safe(item, depth=depth + 1)

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


def _as_dict(value: Any) -> Dict[str, Any]:
    try:
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _version_sort_key(version: Mapping[str, Any]) -> Tuple[int, str, str]:
    try:
        idx = _safe_int(version.get("version_idx"), 0)
        created = _clean_text(
            version.get("created_at")
            or version.get("created")
            or version.get("ts"),
            "",
            120,
        )
        version_id = _clean_text(version.get("version_id"), "", 160)
        return idx, created, version_id
    except Exception:
        return 0, "", ""


# ───────────────────────── Conversation / Blob helpers ─────────────────────────

def _get_conversation(chat_id: Optional[str]) -> Optional[Conversation]:
    if not chat_id:
        return None

    try:
        return db.session.get(Conversation, str(chat_id))
    except Exception:
        try:
            return Conversation.query.get(str(chat_id))
        except Exception:
            return None


def _get_blob(blob_id: object) -> Optional[Blob]:
    if blob_id is None:
        return None

    raw = str(blob_id).strip()

    if not raw:
        return None

    candidates: List[Any] = [raw]

    try:
        candidates.append(int(raw))
    except Exception:
        pass

    for candidate in candidates:
        try:
            blob = db.session.get(Blob, candidate)
            if blob:
                return blob
        except Exception:
            pass

        try:
            blob = Blob.query.get(candidate)
            if blob:
                return blob
        except Exception:
            pass

    return None


def _ensure_conv(chat_id: Optional[str]) -> Conversation:
    """
    Return an existing Conversation or create one.

    Kept for compatibility with existing 2D routes.
    """
    conv = _get_conversation(chat_id)

    if conv is not None:
        return conv

    try:
        conv = Conversation()
        db.session.add(conv)
        db.session.commit()
        return conv

    except Exception:
        try:
            db.session.rollback()
            current_app.logger.exception("create conversation failed")
        except Exception:
            pass

        abort(404, description="conversation create failed")


def _blob_id_from_version(version: Mapping[str, Any]) -> Optional[Any]:
    try:
        direct = (
            version.get("blob_id")
            or version.get("input_blob_id")
            or version.get("file_id")
        )

        if direct:
            return direct

        meta = _as_dict(version.get("meta"))

        direct_meta = (
            meta.get("blob_id")
            or meta.get("input_blob_id")
            or meta.get("file_id")
        )

        if direct_meta:
            return direct_meta

        input_blob = meta.get("input_blob")

        if isinstance(input_blob, dict):
            return (
                input_blob.get("file_id")
                or input_blob.get("blob_id")
                or input_blob.get("id")
            )

        return None

    except Exception:
        return None


def _conversation_has_blob(conv_id: str, blob_id: int) -> bool:
    """
    Security check:
    A DXF Blob may only be served if it is referenced by a version belonging to
    the current conversation.
    """
    try:
        from versioning import list_versions_by_conversation

        items = list_versions_by_conversation(conversation_id=conv_id, kind=None) or []

        for version in items:
            try:
                candidate = _blob_id_from_version(version)

                if candidate is None:
                    continue

                if str(candidate) == str(blob_id):
                    return True

                try:
                    if int(str(candidate)) == int(str(blob_id)):
                        return True
                except Exception:
                    pass

            except Exception:
                continue

    except Exception:
        pass

    return False


# ───────────────────────── Version lookup ─────────────────────────

def _meta_filename(meta: Mapping[str, Any]) -> str:
    try:
        input_blob = meta.get("input_blob")

        if isinstance(input_blob, dict):
            name = (
                input_blob.get("name")
                or input_blob.get("filename")
                or input_blob.get("file_name")
            )
            if name:
                return str(name)

        return str(
            meta.get("filename")
            or meta.get("name")
            or meta.get("file_name")
            or ""
        )

    except Exception:
        return ""


def _meta_mime(meta: Mapping[str, Any]) -> str:
    try:
        input_blob = meta.get("input_blob")

        if isinstance(input_blob, dict):
            mime = input_blob.get("mime") or input_blob.get("content_type")
            if mime:
                return str(mime)

        return str(meta.get("mime") or meta.get("content_type") or "")

    except Exception:
        return ""


def _version_has_dxf_reference(version: Mapping[str, Any]) -> bool:
    try:
        kind = _clean_text(version.get("kind"), "", 120)

        if kind in _DXF_KINDS:
            return True

        label = _clean_text(version.get("label"), "", 240)
        if _is_dxf_name(label):
            return True

        meta = _as_dict(version.get("meta"))

        filename = _meta_filename(meta)
        if _is_dxf_name(filename):
            return True

        if _looks_like_dxf_mime(_meta_mime(meta)):
            if _is_dxf_name(filename) or kind in _DXF_KINDS:
                return True

        url = _clean_text(meta.get("dxf_url") or meta.get("url"), "", 1000)
        if _is_dxf_name(urlsplit(url).path if url else ""):
            return True

        blob_id = _blob_id_from_version(version)
        blob = _get_blob(blob_id)

        if blob:
            if _is_dxf_name(getattr(blob, "filename", "") or ""):
                return True
            if _looks_like_dxf_mime(getattr(blob, "mime", "") or ""):
                return True

        return False

    except Exception:
        return False


def _version_label(version: Mapping[str, Any], meta: Mapping[str, Any], blob: Optional[Blob]) -> str:
    try:
        candidates = [
            version.get("label"),
            _meta_filename(meta),
            getattr(blob, "filename", None) if blob else None,
            "plan.dxf",
        ]

        for candidate in candidates:
            text = _clean_text(candidate, "", 240)
            if text:
                return text

        return "plan.dxf"

    except Exception:
        return "plan.dxf"


def _version_url(version: Mapping[str, Any], meta: Mapping[str, Any]) -> str:
    try:
        for key in ("dxf_url", "url", "content_url", "download_url"):
            value = _clean_text(meta.get(key), "", 1000)
            if value:
                return value

        return ""

    except Exception:
        return ""


def _version_revision(version: Mapping[str, Any], meta: Mapping[str, Any]) -> Any:
    try:
        return (
            version.get("version_idx")
            or version.get("version_id")
            or version.get("id")
            or meta.get("rev")
            or meta.get("revision")
            or 0
        )
    except Exception:
        return 0


def _find_latest_dxf_version(conv_id: str) -> Optional[Dict[str, Any]]:
    """
    Find the newest neutral version with DXF relevance.

    Compatible with the current neutral versioning structure:
    - kind
    - label
    - blob_id/input_blob_id
    - meta.filename
    - meta.input_blob
    - meta.content_url/download_url/dxf_url
    """
    try:
        from versioning import list_versions_by_conversation
    except Exception:
        return None

    try:
        items: List[Dict[str, Any]] = (
            list_versions_by_conversation(conversation_id=conv_id, kind=None) or []
        )
    except Exception:
        return None

    candidates = []

    for item in items:
        try:
            if not isinstance(item, dict):
                continue

            if _version_has_dxf_reference(item):
                candidates.append(item)

        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(key=_version_sort_key, reverse=True)

    version = candidates[0]
    meta = _as_dict(_json_safe(version.get("meta") or {}))
    blob_id = _blob_id_from_version(version)
    blob = _get_blob(blob_id)

    url = _version_url(version, meta)

    # If no URL was recorded, the route caller can later build dxf_blob URL from blob_id.
    info = {
        "version": version,
        "blob_id": blob_id,
        "input_blob_id": blob_id,
        "url": url,
        "meta": meta,
        "label": _version_label(version, meta, blob),
        "rev": _version_revision(version, meta),
        "created_at": (
            version.get("created_at")
            or version.get("created")
            or version.get("ts")
            or ""
        ),
    }

    return info


# ───────────────────────── Cache / frame headers ─────────────────────────

def _cache_headers(resp, strong: bool = False):
    """
    Unified cache headers for 2D viewer assets.
    """
    try:
        dev = str(current_app.config.get("FLASK_ENV", "") or "").lower().startswith("dev")

        if dev:
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers.setdefault("Pragma", "no-cache")
            resp.headers.setdefault("Expires", "0")
        else:
            if strong:
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                resp.headers["Cache-Control"] = "public, max-age=86400"

        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")

    except Exception:
        pass

    return resp


def _frame_headers(resp):
    """
    Frame headers for iframe pages.
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

        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")

    except Exception:
        pass

    return resp


# ───────────────────────── CAD service config ─────────────────────────

def _cfg_cad_internal_base() -> str:
    """
    Internal base URL for server-to-server CAD requests.
    """
    try:
        url = str(current_app.config.get("CADVIEWER_BASE_URL") or "").strip().rstrip("/")
        return url or _DEFAULT_CAD_INTERNAL_BASE
    except Exception:
        return _DEFAULT_CAD_INTERNAL_BASE


def _cfg_cad_public_base() -> str:
    """
    Browser-facing CAD base URL for iframe embeds.
    """
    try:
        url = str(current_app.config.get("CADVIEWER_PUBLIC_URL") or "").strip().rstrip("/")
        return url or _DEFAULT_CAD_PUBLIC_BASE
    except Exception:
        return _DEFAULT_CAD_PUBLIC_BASE


# ───────────────────────── HTTP helpers ─────────────────────────

def _http_get_json(url: str, timeout: float = 10.0) -> Tuple[bool, Any]:
    """
    GET JSON using requests first, urllib fallback.
    """
    try:
        import requests

        response = requests.get(url, timeout=timeout)

        if response.ok:
            try:
                return True, response.json()
            except Exception:
                return False, None

        return False, getattr(response, "text", None)

    except Exception:
        try:
            from urllib.request import Request, urlopen

            request_obj = Request(url, headers={"Accept": "application/json"})

            with urlopen(request_obj, timeout=timeout) as response:
                data = response.read()

                try:
                    return True, json.loads(data.decode("utf-8"))
                except Exception:
                    return False, None

        except Exception:
            return False, None


def _http_post_file(
    url: str,
    field_name: str,
    filename: str,
    data: bytes,
    content_type: str = "application/dxf",
    timeout: float = 30.0,
) -> Tuple[bool, Any]:
    """
    POST multipart file. Prefer requests, fallback to urllib.
    """
    try:
        import requests

        files = {
            field_name: (
                filename,
                data,
                content_type,
            )
        }

        response = requests.post(url, files=files, timeout=timeout)

        if response.ok:
            try:
                return True, response.json()
            except Exception:
                return True, None

        return False, getattr(response, "text", "")

    except Exception:
        try:
            from urllib.request import Request, urlopen

            boundary = "----cadFormBoundary7MA4YWxkTrZu0gW"
            body = [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                data,
                f"\r\n--{boundary}--\r\n".encode("utf-8"),
            ]

            payload = b"".join(body)

            request_obj = Request(url, data=payload, method="POST")
            request_obj.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            request_obj.add_header("Content-Length", str(len(payload)))

            with urlopen(request_obj, timeout=timeout) as response:
                output = response.read()

                try:
                    return True, json.loads(output.decode("utf-8"))
                except Exception:
                    return True, None

        except Exception:
            return False, None


# ───────────────────────── CAD file naming / bytes ─────────────────────────

def _safe_name(value: str) -> str:
    """
    Sanitize a CAD filename.

    Allows only:
    - letters
    - digits
    - dot
    - underscore
    - dash
    """
    try:
        base = os.path.basename(value or "plan.dxf").strip()

        if not base:
            base = "plan.dxf"

        if not base.lower().endswith(".dxf"):
            base = base + ".dxf"

        base = re.sub(r"[^a-zA-Z0-9._-]+", "_", base)
        base = re.sub(r"_+", "_", base).strip("._-")

        if not base.lower().endswith(".dxf"):
            base = base + ".dxf"

        if len(base) > _MAX_CAD_FILENAME_LENGTH:
            root, ext = os.path.splitext(base)
            base = root[: _MAX_CAD_FILENAME_LENGTH - len(ext)] + ext

        return base or "plan.dxf"

    except Exception:
        return "plan.dxf"


def _build_cad_name(conv_id: str, label: str, rev: Any) -> str:
    try:
        clean_conv_id = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(conv_id or "chat")).strip("._-")
        if not clean_conv_id:
            clean_conv_id = "chat"

        clean_label = _safe_name(label or "plan.dxf")
        clean_rev = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(rev or "0")).strip("._-")
        suffix = f"_r{clean_rev}" if clean_rev else ""

        name = f"{clean_conv_id}{suffix}_{clean_label}"
        name = re.sub(r"\.dxf\.dxf$", ".dxf", name, flags=re.IGNORECASE)

        if len(name) > _MAX_CAD_FILENAME_LENGTH:
            root, ext = os.path.splitext(name)
            name = root[: _MAX_CAD_FILENAME_LENGTH - len(ext)] + ext

        return name or "plan.dxf"

    except Exception:
        return _safe_name(label or "plan.dxf")


def _load_blob_bytes(blob_id: object) -> Optional[bytes]:
    """
    Load bytes from Blob by id.

    Supports integer-like and string/UUID-like ids.
    """
    blob = _get_blob(blob_id)

    if not blob:
        return None

    try:
        data = getattr(blob, "data", None)

        if not data:
            return None

        if isinstance(data, bytes):
            return data

        if isinstance(data, bytearray):
            return bytes(data)

        return bytes(data)

    except Exception:
        return None


def _fetch_local_path_bytes(path: str) -> Optional[bytes]:
    """
    Fetch local path bytes through WEB_INTERNAL_URL.

    Only same-origin relative paths are allowed.
    """
    try:
        base = str(current_app.config.get("WEB_INTERNAL_URL") or "").strip().rstrip("/")

        if not base:
            return None

        if not _is_local_path(path):
            return None

        url = base + path

        try:
            import requests

            response = requests.get(url, timeout=15)

            if response.ok:
                return response.content

            return None

        except Exception:
            from urllib.request import urlopen

            with urlopen(url, timeout=15) as response:
                return response.read()

    except Exception:
        return None