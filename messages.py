# /services/app/messages.py
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app
from extensions import db

# ───────────────────────── internals ─────────────────────────

_MEM_TEMPLATES: Dict[str, Dict[str, Any]] = {}
_MEM_STATE: Dict[str, Dict[str, Any]] = {}

# eingebaute Default-Templates (Memory, optional Upsert in DB via register_template)
_BUILTIN_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "project_welcome": {
        "key": "project_welcome",
        "version": 1,
        "renderer": "InfoCard",
        "title": "AI-Chat - Wichtiger Hinweis",
        "is_active": True,
        "schema_json": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "md": {"type": "string"},
            },
            "required": [],
        },
    },
    "info_card": {
        "key": "info_card",
        "version": 1,
        "renderer": "InfoCard",
        "title": "Info",
        "is_active": True,
        "schema_json": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "md": {"type": "string"},
                "image_url": {"type": "string"},
            },
            "required": [],
        },
    },
    "error_card": {
        "key": "error_card",
        "version": 1,
        "renderer": "InfoCard",
        "title": "Fehler",
        "is_active": True,
        "schema_json": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "md": {"type": "string"},
                "severity": {"type": "string"},
            },
            "required": ["title", "md"],
        },
    },
    "download_card": {
        "key": "download_card",
        "version": 1,
        "renderer": "DownloadCard",
        "title": "Download",
        "is_active": True,
        "schema_json": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "href": {"type": "string"},
                "mime": {"type": "string"},
            },
            "required": ["label", "href"],
        },
    },
    "missing_slots": {
        "key": "missing_slots",
        "version": 1,
        "renderer": "InfoList",
        "title": "Angaben fehlen",
        "is_active": True,
        "schema_json": {
            "type": "object",
            "properties": {
                "missing": {"type": "array", "items": {"type": "string"}},
                "tips": {"type": "array", "items": {"type": "string"}},
                "example_bbox": {"type": "string"},
            },
            "required": ["missing"],
        },
    },
    "choice_list": {
        "key": "choice_list",
        "version": 1,
        "renderer": "ChoiceList",
        "title": "Auswahl",
        "is_active": True,
        "schema_json": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "choices": {"type": "array"},
            },
            "required": ["choices"],
        },
    },
    # Viewer-Karte als Fallback, falls Seeds fehlen
    "speckle_viewer": {
        "key": "speckle_viewer",
        "version": 1,
        "renderer": "SpeckleViewerCard",
        "title": "3D-Viewer",
        "is_active": True,
        "schema_json": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "stream_id": {"type": "string"},
                "model_id": {"type": "string"},
                "caption": {"type": "string"},
            },
            "required": [],
            "additionalProperties": True,
        },
    },
    # Formular zur Projektinfo-Erfassung
    "project_info_form": {
        "key": "project_info_form",
        "version": 1,
        "renderer": "ProjectInfoForm",
        "title": "Projektinformationen",
        "is_active": True,
        "schema_json": {
            "type": "object",
            "properties": {
                "bauort": {"type": "string"},
                "bauherr": {"type": "string"},
                "wohnort": {"type": "string"},
                "telefon": {"type": "string"},
                "email": {"type": "string"},
                "notizen": {"type": "string"},
            },
            "required": [],
            "additionalProperties": True,
        },
    },
    # Zusammenfassung der Projektinfo als einfache Karte
    "project_info_summary": {
        "key": "project_info_summary",
        "version": 1,
        "renderer": "InfoCard",
        "title": "Projektinfo",
        "is_active": True,
        "schema_json": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "md": {"type": "string"},
            },
            "required": ["md"],
        },
    },
}


def _now_iso() -> str:
    try:
        return datetime.utcnow().isoformat() + "Z"
    except Exception:
        return ""


def _log(level: str, msg: str, *args) -> None:
    try:
        logger = getattr(current_app, "logger", None)
        if logger and hasattr(logger, level):
            getattr(logger, level)(msg, *args)
    except Exception:
        pass


def _get_models():
    """
    Versucht dynamisch optionale Modelle aus models.py zu laden.
    Rückgabe-Dict kann Keys 'MessageTemplate' und 'ConversationState' enthalten.
    """
    try:
        import models as _m  # noqa
    except Exception as ex:
        _log("warning", "messages: cannot import models (%s)", ex)
        return {}

    out = {}
    try:
        mt = getattr(_m, "MessageTemplate", None)
        if mt is not None:
            out["MessageTemplate"] = mt
    except Exception:
        pass
    try:
        cs = getattr(_m, "ConversationState", None)
        if cs is not None:
            out["ConversationState"] = cs
    except Exception:
        pass
    return out


def _ensure_builtin_templates() -> None:
    """
    Stellt eingebaute Templates in Memory bereit (idempotent).
    Schreibt NICHT zwingend in DB (das ist optional und langsam).
    """
    try:
        for k, t in _BUILTIN_TEMPLATES.items():
            if k not in _MEM_TEMPLATES:
                _MEM_TEMPLATES[k] = {
                    "key": t["key"],
                    "version": int(t.get("version") or 1),
                    "renderer": t.get("renderer") or "InfoCard",
                    "title": t.get("title") or t["key"],
                    "schema_json": dict(t.get("schema_json") or {}),
                    "is_active": bool(t.get("is_active", True)),
                    "created_at": _now_iso(),
                }
    except Exception:
        pass


def _ensure_seed_loaded() -> None:
    """
    Lädt eingebaute Default-Templates und optionale Seeds aus Config:
      • TEMPLATE_SEED: List[dict]  (direkt)
      • TEMPLATE_SEED_PATH: Pfad zu JSON-Datei mit List[dict]
    Idempotent: überschreibt existierende Keys nicht.
    """
    try:
        if getattr(_ensure_seed_loaded, "_done", False):
            return

        _ensure_builtin_templates()

        seeds: List[Dict[str, Any]] = []
        try:
            cfg_list = current_app.config.get("TEMPLATE_SEED")
            if isinstance(cfg_list, list):
                seeds = [x for x in cfg_list if isinstance(x, dict)]
        except Exception:
            pass

        if not seeds:
            path = (current_app.config.get("TEMPLATE_SEED_PATH") or "").strip()
            if path:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            seeds = [x for x in data if isinstance(x, dict)]
                except Exception as ex:
                    _log("warning", "messages: cannot load TEMPLATE_SEED_PATH: %s", ex)

        for row in seeds or []:
            try:
                key = str(row.get("key") or "").strip()
                if not key:
                    continue
                schema = row.get("schema") or row.get("schema_json") or {}
                renderer = str(row.get("renderer") or "").strip() or "InfoCard"
                title = str(row.get("title") or "").strip() or key
                version = int(row.get("version") or 1)
                is_active = bool(row.get("is_active", True))
                if key not in _MEM_TEMPLATES:
                    _MEM_TEMPLATES[key] = {
                        "key": key,
                        "version": version,
                        "schema_json": schema if isinstance(schema, dict) else {},
                        "renderer": renderer,
                        "title": title,
                        "is_active": is_active,
                        "created_at": _now_iso(),
                    }
            except Exception:
                continue

        setattr(_ensure_seed_loaded, "_done", True)
    except Exception:
        pass


# ───────────────────────── Template-Registry ─────────────────────────

def register_template(
    *,
    key: str,
    schema_json: Optional[Dict[str, Any]] = None,
    renderer: str = "InfoCard",
    title: Optional[str] = None,
    version: int = 1,
    is_active: bool = True,
) -> Dict[str, Any]:
    """
    Registriert/aktualisiert ein Template. Versucht DB, fällt auf Memory zurück.
    Rückgabe: flaches Dict.
    """
    key = (key or "").strip()
    if not key:
        raise ValueError("template key required")

    schema_json = schema_json or {}
    payload = {
        "key": key,
        "version": int(version or 1),
        "schema_json": schema_json if isinstance(schema_json, dict) else {},
        "renderer": (renderer or "InfoCard").strip() or "InfoCard",
        "title": (title or key).strip() or key,
        "is_active": bool(is_active),
        "created_at": _now_iso(),
    }

    models = _get_models()
    MT = models.get("MessageTemplate")

    if MT is None:
        _MEM_TEMPLATES[key] = payload
        return payload

    try:
        row = MT.query.filter_by(key=key).one_or_none()
        if row is None:
            row = MT(key=key)
            db.session.add(row)
        row.version = payload["version"]
        row.renderer = payload["renderer"]
        row.title = payload["title"]
        row.schema_json = payload["schema_json"]
        row.is_active = payload["is_active"]
        db.session.commit()
        return {
            "key": row.key,
            "version": row.version,
            "renderer": row.renderer,
            "title": row.title,
            "schema_json": dict(row.schema_json or {}),
            "is_active": bool(row.is_active),
            "created_at": payload["created_at"],
        }
    except Exception as ex:
        db.session.rollback()
        _log("warning", "messages: register_template DB failed, use memory (%s)", ex)
        _MEM_TEMPLATES[key] = payload
        return payload


def get_template(key: str) -> Optional[Dict[str, Any]]:
    """
    Holt Template per Key. DB bevorzugt, sonst Seed/Fallback.
    """
    key = (key or "").strip()
    if not key:
        return None

    # DB
    try:
        MT = _get_models().get("MessageTemplate")
        if MT is not None:
            row = MT.query.filter_by(key=key, is_active=True).one_or_none()
            if row:
                return {
                    "key": row.key,
                    "version": int(row.version or 1),
                    "renderer": row.renderer or "InfoCard",
                    "title": row.title or row.key,
                    "schema_json": dict(row.schema_json or {}),
                    "is_active": bool(row.is_active),
                }
    except Exception as ex:
        _log("warning", "messages: get_template DB failed (%s)", ex)

    # Memory
    _ensure_seed_loaded()
    t = _MEM_TEMPLATES.get(key)
    return dict(t) if t else None


def list_templates() -> List[Dict[str, Any]]:
    """
    Liste aktiver Templates. DB bevorzugt, Seed/Mem als Fallback.
    """
    out: List[Dict[str, Any]] = []
    MT = _get_models().get("MessageTemplate")
    if MT is not None:
        try:
            for row in MT.query.filter_by(is_active=True).order_by(MT.key.asc()).all():
                try:
                    out.append({
                        "key": row.key,
                        "version": int(row.version or 1),
                        "renderer": row.renderer or "InfoCard",
                        "title": row.title or row.key,
                        "schema_json": dict(row.schema_json or {}),
                        "is_active": bool(row.is_active),
                    })
                except Exception:
                    continue
            if out:
                return out
        except Exception as ex:
            _log("warning", "messages: list_templates DB failed (%s)", ex)

    _ensure_seed_loaded()
    try:
        keys = sorted(_MEM_TEMPLATES.keys())
        for k in keys:
            out.append(dict(_MEM_TEMPLATES[k]))
    except Exception:
        pass
    return out


# ───────────────────────── Payload-Validierung ─────────────────────────

def _validate_type(value: Any, expected: Any) -> bool:
    """
    Sehr einfache Validatoren, falls jsonschema nicht installiert ist.
    Unterstützt: type: string|number|integer|boolean|object|array|null
    """
    try:
        t = str(expected or "").lower()
        if t == "string":
            return isinstance(value, str)
        if t == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if t == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if t == "boolean":
            return isinstance(value, bool)
        if t == "object":
            return isinstance(value, dict)
        if t == "array":
            return isinstance(value, list)
        if t == "null":
            return value is None
        return True
    except Exception:
        return True


def validate_payload(template: Dict[str, Any], payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Minimaler JSON-Schema Support:
      - required[]
      - properties{ name: {type} }
    Wenn 'jsonschema' verfügbar ist, wird es bevorzugt genutzt.
    """
    try:
        # jsonschema nutzen, wenn vorhanden
        try:
            import jsonschema  # type: ignore
            schema = template.get("schema_json") or {}
            jsonschema.validate(instance=payload, schema=schema)  # kann Exception werfen
            return True, []
        except Exception:
            # Fallback: Basic-Checks
            schema = template.get("schema_json") or {}
            required = schema.get("required") or []
            props = schema.get("properties") or {}
            errors: List[str] = []

            # required
            try:
                for name in required:
                    if name not in payload:
                        errors.append(f"missing: {name}")
            except Exception:
                pass

            # types
            try:
                for name, spec in props.items():
                    if name in payload and isinstance(spec, dict) and "type" in spec:
                        if not _validate_type(payload[name], spec.get("type")):
                            errors.append(f"type mismatch: {name} expects {spec.get('type')}")
            except Exception:
                pass

            return (len(errors) == 0), errors
    except Exception as ex:
        _log("warning", "messages: validate_payload failed (%s)", ex)
        return True, []  # lieber nicht blockieren


# ───────────────────────── Conversation State ─────────────────────────

def get_state(conversation_id: str) -> Dict[str, Any]:
    """
    Liefert gespeicherten State als Dict.
    Bevorzugt DB, fällt auf Memory zurück.
    """
    if not conversation_id:
        return {}

    CS = _get_models().get("ConversationState")
    if CS is None:
        return dict(_MEM_STATE.get(conversation_id) or {})

    try:
        row = CS.query.filter_by(conversation_id=conversation_id).one_or_none()
        return dict(row.state_json or {}) if row else {}
    except Exception as ex:
        _log("warning", "messages: get_state DB failed (%s)", ex)
        return dict(_MEM_STATE.get(conversation_id) or {})


def merge_state(conversation_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merged Patch in bestehenden State und speichert.
    """
    if not conversation_id:
        return {}

    CS = _get_models().get("ConversationState")
    if CS is None:
        try:
            base = dict(_MEM_STATE.get(conversation_id) or {})
            base.update(patch or {})
            _MEM_STATE[conversation_id] = base
            return dict(base)
        except Exception:
            return dict(_MEM_STATE.get(conversation_id) or {})

    try:
        row = CS.query.filter_by(conversation_id=conversation_id).one_or_none()
        if row is None:
            row = CS(conversation_id=conversation_id, state_json=dict(patch or {}))
            db.session.add(row)
        else:
            base = dict(row.state_json or {})
            base.update(patch or {})
            row.state_json = base
        db.session.commit()
        return dict(row.state_json or {})
    except Exception as ex:
        db.session.rollback()
        _log("warning", "messages: merge_state DB failed (%s)", ex)
        # Memory-Fallback
        try:
            base = dict(_MEM_STATE.get(conversation_id) or {})
            base.update(patch or {})
            _MEM_STATE[conversation_id] = base
            return dict(base)
        except Exception:
            return {}


# ───────────────────────── Transcript Helpers ─────────────────────────

def _append_card_message(conv, template_key: str, payload: Dict[str, Any], role: str, trace: Optional[List[str]]):
    """
    Fügt Card als service/assistant Nachricht an das Transcript.
    Erwartet Conversation-Objekt mit append_message(...).
    """
    try:
        meta = {
            "type": "card",
            "template": template_key,
            "payload": payload or {},
        }
        return conv.append_message(role=role, text="", attachments=[], trace=trace or [], meta=meta)
    except Exception as ex:
        _log("warning", "messages: append_card_message failed (%s)", ex)
        return None


def _sanitize_role(v: Any, fallback: str = "service") -> str:
    try:
        s = str(v or fallback).strip().lower()
        return s if s in {"service", "assistant", "system"} else fallback
    except Exception:
        return fallback


def _sanitize_trace(v: Any) -> List[str]:
    try:
        if not isinstance(v, list):
            return []
        out: List[str] = []
        for x in v:
            try:
                s = str(x).strip()
                if s:
                    out.append(s)
            except Exception:
                continue
        return out[:10]
    except Exception:
        return []


def post_card_message(
    *,
    conversation,
    template_key: str,
    payload: Dict[str, Any] | None = None,
    role: str = "service",
    trace: Optional[List[str]] = None,
    validate: bool = True,
) -> Dict[str, Any]:
    """
    Holt Template, validiert Payload, hängt Card an Transcript und persistiert.
    Rückgabe: {'ok':bool,'error':str|None,'message':{...}}
    """
    try:
        tpl = get_template(template_key)
        if tpl is None:
            _log("warning", "messages: template '%s' not found, posting raw payload", template_key)
            msg = _append_card_message(conversation, template_key, payload or {}, _sanitize_role(role), _sanitize_trace(trace))
            try:
                db.session.add(conversation); db.session.commit()
            except Exception:
                db.session.rollback()
            return {"ok": True, "error": None, "message": msg}

        if validate:
            ok, errs = validate_payload(tpl, payload or {})
            if not ok:
                err_text = "; ".join(errs)[:400]
                _log("warning", "messages: payload invalid for '%s': %s", template_key, err_text)
                msg = _append_card_message(conversation, "error_card", {
                    "title": f"Ungültige Daten für {template_key}",
                    "md": f"Validierung fehlgeschlagen: {err_text}",
                }, _sanitize_role(role), _sanitize_trace((trace or []) + ["validator"]))
                try:
                    db.session.add(conversation); db.session.commit()
                except Exception:
                    db.session.rollback()
                return {"ok": False, "error": err_text, "message": msg}

        msg = _append_card_message(conversation, template_key, payload or {}, _sanitize_role(role), _sanitize_trace(trace))
        try:
            db.session.add(conversation); db.session.commit()
        except Exception:
            db.session.rollback()
        return {"ok": True, "error": None, "message": msg}
    except Exception as ex:
        _log("warning", "messages: post_card_message failed (%s)", ex)
        return {"ok": False, "error": str(ex), "message": None}


def post_text_message(
    *,
    conversation,
    text: str,
    role: str = "assistant",
    trace: Optional[List[str]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Hängt einfache Textnachricht an und persistiert.
    """
    try:
        msg = conversation.append_message(
            role=_sanitize_role(role),
            text=str(text or ""),
            attachments=attachments or [],
            trace=_sanitize_trace(trace),
            meta=meta or {},
        )
        try:
            db.session.add(conversation); db.session.commit()
        except Exception:
            db.session.rollback()
        return {"ok": True, "message": msg}
    except Exception as ex:
        _log("warning", "messages: post_text_message failed (%s)", ex)
        return {"ok": False, "message": None}


# ───────────────────────── Aktionen aus ChatAI ─────────────────────────

def run_actions(
    *,
    conversation,
    actions: List[Dict[str, Any]] | None,
) -> Dict[str, Any]:
    """
    Führt Standardaktionen aus:
      - post_message {template, payload, role?, trace?}
      - update_state {patch}
      - trigger_job {service, op, args}  -> nur persistenter Job-Stub im Transcript
    Rückgabe: {results:[...]}
    """
    results: List[Dict[str, Any]] = []
    for act in actions or []:
        try:
            typ = str(act.get("type") or "").strip().lower()
            if typ == "post_message":
                res = post_card_message(
                    conversation=conversation,
                    template_key=str(act.get("template") or ""),
                    payload=act.get("payload") or {},
                    role=_sanitize_role(act.get("role") or "service"),
                    trace=_sanitize_trace(act.get("trace")),
                    validate=True,
                )
                results.append({"type": "post_message", "ok": bool(res.get("ok")), "error": res.get("error")})
                continue

            if typ == "update_state":
                patch = act.get("patch") or {}
                state = merge_state(conversation.id, patch)
                try:
                    conversation.append_message(role="service", text="", trace=["state"], meta={"type": "state_patch", "patch": patch})
                except Exception:
                    pass
                try:
                    db.session.add(conversation); db.session.commit()
                except Exception:
                    db.session.rollback()
                results.append({"type": "update_state", "ok": True, "state": state})
                continue

            if typ == "trigger_job":
                try:
                    conversation.append_message(
                        role="service",
                        text="",
                        trace=["job"],
                        meta={"type": "job_request", "job": {
                            "service": act.get("service"),
                            "op": act.get("op"),
                            "args": act.get("args") or {},
                            "ts": _now_iso(),
                        }},
                    )
                except Exception:
                    pass
                try:
                    db.session.add(conversation); db.session.commit()
                except Exception:
                    db.session.rollback()
                results.append({"type": "trigger_job", "ok": True})
                continue

            _log("warning", "messages: unknown action type '%s'", typ)
            results.append({"type": typ or "unknown", "ok": False, "error": "unknown action"})
        except Exception as ex:
            _log("warning", "messages: run_actions item failed (%s)", ex)
            results.append({"type": "error", "ok": False, "error": str(ex)})

    return {"results": results}


# Modul-Init: Default-Templates in Memory bereitstellen
try:
    _ensure_builtin_templates()
except Exception:
    pass
