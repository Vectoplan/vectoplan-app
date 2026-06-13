# /services/app/versioning.py
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
from copy import deepcopy
from datetime import datetime
from hashlib import sha256

from extensions import db
from models import Conversation, Blob

# ───────────────────────── helpers ─────────────────────────

def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def _ensure_list(x) -> List[dict]:
    if isinstance(x, list):
        return x
    return []

def _iter_version_msgs(conv: Conversation) -> Iterable[dict]:
    for msg in _ensure_list(conv.transcript):
        try:
            meta = msg.get("meta") or {}
            if meta.get("type") == "version" and isinstance(meta.get("version"), dict):
                yield msg
        except Exception:
            continue

def _next_version_idx(conv: Conversation, kind: str) -> int:
    max_idx = 0
    for msg in _iter_version_msgs(conv):
        v = msg.get("meta", {}).get("version", {})
        if v.get("kind") == kind:
            try:
                max_idx = max(max_idx, int(v.get("version_idx") or 0))
            except Exception:
                continue
    return max_idx + 1

def _artifact_id(conv_id: str, kind: str) -> str:
    # stabil, ohne extra Tabelle
    base = f"{conv_id}:{kind}"
    return sha256(base.encode("utf-8")).hexdigest()[:16]

def _compact_version_dict(v: dict) -> dict:
    """Schlanke Darstellung für API-Listen."""
    return {
        "version_id": v.get("version_id"),
        "artifact_id": v.get("artifact_id"),
        "kind": v.get("kind"),
        "label": v.get("label"),
        "version_idx": v.get("version_idx"),
        "created_at": v.get("created_at"),
        "speckle": {
            "project_id": v.get("speckle_project_id"),
            "model_id": v.get("speckle_model_id"),
            "version_id": v.get("speckle_version_id"),
        },
        "input_blob_id": v.get("input_blob_id"),
        "status": v.get("status"),
    }

# ───────────────────────── public API ─────────────────────────

def record_version(
    *,
    conversation_id: str,
    kind: str,
    label: str = "",
    source_message_id: Optional[str] = None,
    input_blob_id: Optional[str] = None,
    speckle_project_id: Optional[str] = None,
    speckle_model_id: Optional[str] = None,
    speckle_version_id: Optional[str] = None,
    status: str = "ok",
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Legt eine Version als 'service'-Nachricht im Transcript an.
    Rückgabe: {"artifact_id", "version_id", "version_idx"}.
    """
    conv = Conversation.query.get(conversation_id)
    if not conv:
        raise ValueError("conversation not found")

    # Eingabe-BLOB-Metadaten anreichern
    if input_blob_id:
        try:
            b = Blob.query.get(str(input_blob_id))
            if b:
                meta = dict(meta or {})
                meta.setdefault("input_blob", {
                    "file_id": b.id, "name": b.filename, "mime": b.mime, "size": b.size, "sha256": b.sha256
                })
        except Exception:
            pass

    vid = sha256(f"{conversation_id}-{_utcnow_iso()}-{kind}-{label}".encode("utf-8")).hexdigest()[:20]
    aid = _artifact_id(conversation_id, kind)
    idx = _next_version_idx(conv, kind)

    version_payload: Dict[str, Any] = {
        "version_id": vid,
        "artifact_id": aid,
        "kind": kind,
        "label": label or kind,
        "version_idx": idx,
        "created_at": _utcnow_iso(),
        "source_message_id": source_message_id,
        "input_blob_id": input_blob_id,
        "speckle_project_id": speckle_project_id,
        "speckle_model_id": speckle_model_id,
        "speckle_version_id": speckle_version_id,
        "status": status,
        "meta": meta or {},
    }

    # als service message ablegen
    try:
        conv.append_message(
            role="service",
            text="",
            trace=[kind],
            meta={"type": "version", "version": deepcopy(version_payload)},
        )
        db.session.add(conv)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {"artifact_id": aid, "version_id": vid, "version_idx": idx}

def list_versions_by_conversation(*, conversation_id: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    conv = Conversation.query.get(conversation_id)
    if not conv:
        return []
    out: List[Dict[str, Any]] = []
    for msg in _iter_version_msgs(conv):
        v = msg.get("meta", {}).get("version", {})
        if kind and v.get("kind") != kind:
            continue
        out.append(_compact_version_dict(v))
    # sortiere neueste zuerst
    out.sort(key=lambda x: (int(x.get("version_idx") or 0), x.get("created_at") or ""), reverse=True)
    return out

def delete_version(*, version_id: str) -> bool:
    """
    Entfernt die Version-Nachricht aus dem Transcript.
    """
    # Suche Conversation, die diese Version enthält
    q = Conversation.query
    found: Optional[Conversation] = None
    for conv in q:  # kleine Datenmengen; bei Wachstum durch SQL ersetzen
        for msg in _iter_version_msgs(conv):
            v = msg.get("meta", {}).get("version", {})
            if v.get("version_id") == version_id:
                found = conv
                break
        if found:
            break

    if not found:
        return False

    # herausfiltern
    try:
        new_t: List[dict] = []
        for msg in _ensure_list(found.transcript):
            keep = True
            try:
                meta = msg.get("meta") or {}
                if meta.get("type") == "version":
                    v = meta.get("version") or {}
                    if v.get("version_id") == version_id:
                        keep = False
            except Exception:
                pass
            if keep:
                new_t.append(msg)
        found.transcript = new_t
        db.session.add(found)
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        raise

def prune(*, conversation_id: str, kind: str, keep: int = 10) -> None:
    """
    Hält höchstens 'keep' Versionen eines Kind-Typs im Transcript.
    Ältere werden entfernt.
    """
    conv = Conversation.query.get(conversation_id)
    if not conv:
        return

    # sammle Ziele
    versions: List[Tuple[int, str]] = []  # (idx, version_id)
    for msg in _iter_version_msgs(conv):
        v = msg.get("meta", {}).get("version", {})
        if v.get("kind") == kind:
            try:
                versions.append((int(v.get("version_idx") or 0), v.get("version_id")))
            except Exception:
                continue
    versions.sort(key=lambda t: t[0], reverse=True)

    if len(versions) <= keep:
        return

    to_remove = {vid for _, vid in versions[keep:]}
    try:
        new_t: List[dict] = []
        for msg in _ensure_list(conv.transcript):
            rm = False
            try:
                meta = msg.get("meta") or {}
                if meta.get("type") == "version":
                    v = meta.get("version") or {}
                    if v.get("kind") == kind and v.get("version_id") in to_remove:
                        rm = True
            except Exception:
                pass
            if not rm:
                new_t.append(msg)
        conv.transcript = new_t
        db.session.add(conv)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
