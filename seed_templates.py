# /services/app/seed_templates.py
from __future__ import annotations

"""
Seed-Quelle für Nachrichtentemplates.

Funktionen:
- get_default_templates() -> list[dict]
- wire_app_defaults(app)  -> setzt TEMPLATE_SEED in app.config, falls leer
- apply_seed_to_db(app=None, overwrite=False) -> schreibt Seeds in DB (über messages.register_template)
- write_seed_file(path)   -> schreibt Seeds als JSON-Liste auf Disk

Aufruf als Script:
  python -m seed_templates             # no-op, nur Ausgabe
  python -m seed_templates --write seeds.json
  python -m seed_templates --to-db     # benötigt Flask-App-Kontext
"""

from typing import Any, Dict, List, Optional
import json
import sys

# ───────────────────────── Seeds ─────────────────────────

def _schema_project_welcome() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "wfs_url": {"type": "string"},
            "layer": {"type": "string"},
            "hint": {"type": "string"},
        },
        "required": [],
        "additionalProperties": True,
    }

def _schema_missing_slots() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "missing": {"type": "array", "items": {"type": "string"}},
            "tips": {"type": "array", "items": {"type": "string"}},
            "example_bbox": {"type": "string"},
        },
        "required": ["missing"],
        "additionalProperties": True,
    }

def _schema_info_card() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "md": {"type": "string"},
            "image_url": {"type": "string"},
        },
        "required": ["title", "md"],
        "additionalProperties": True,
    }

def _schema_download_card() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "href": {"type": "string"},
            "mime": {"type": "string"},
        },
        "required": ["label", "href"],
        "additionalProperties": True,
    }

def _schema_speckle_viewer() -> Dict[str, Any]:
    # minimal: mindestens eins von url/stream_id/model_id erlaubt
    return {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "stream_id": {"type": "string"},
            "model_id": {"type": "string"},
            "caption": {"type": "string"},
        },
        "required": [],
        "additionalProperties": True,
    }

def _schema_error_card() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "md": {"type": "string"},
        },
        "required": ["md"],
        "additionalProperties": True,
    }

def _schema_project_info_form() -> Dict[str, Any]:
    # Formular für Projektinformationen
    return {
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
    }

def _schema_project_info_summary() -> Dict[str, Any]:
    # Zusammenfassung als Markdown, wird mit InfoCard gerendert
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "md": {"type": "string"},
        },
        "required": ["md"],
        "additionalProperties": True,
    }

def get_default_templates() -> List[Dict[str, Any]]:
    """
    Liefert eine Liste aus Template-Deskriptoren:
      { key, version, renderer, title, is_active, schema_json }
    """
    try:
        return [
            {
                "key": "project_welcome",
                "version": 1,
                "renderer": "ProjectWelcome",
                "title": "Projektstart",
                "is_active": True,
                "schema_json": _schema_project_welcome(),
            },
            {
                "key": "project_info_form",
                "version": 1,
                "renderer": "ProjectInfoForm",
                "title": "Projektinformationen",
                "is_active": True,
                "schema_json": _schema_project_info_form(),
            },
            {
                "key": "project_info_summary",
                "version": 1,
                "renderer": "InfoCard",
                "title": "Projektinfo",
                "is_active": True,
                "schema_json": _schema_project_info_summary(),
            },
            {
                "key": "missing_slots",
                "version": 1,
                "renderer": "MissingSlots",
                "title": "Fehlende Angaben",
                "is_active": True,
                "schema_json": _schema_missing_slots(),
            },
            {
                "key": "info_card",
                "version": 1,
                "renderer": "InfoCard",
                "title": "Info",
                "is_active": True,
                "schema_json": _schema_info_card(),
            },
            {
                "key": "download_card",
                "version": 1,
                "renderer": "DownloadCard",
                "title": "Download",
                "is_active": True,
                "schema_json": _schema_download_card(),
            },
            {
                "key": "speckle_viewer",
                "version": 1,
                "renderer": "SpeckleViewerCard",
                "title": "3D-Viewer",
                "is_active": True,
                "schema_json": _schema_speckle_viewer(),
            },
            {
                "key": "error_card",
                "version": 1,
                "renderer": "ErrorCard",
                "title": "Fehler",
                "is_active": True,
                "schema_json": _schema_error_card(),
            },
        ]
    except Exception:
        # Fallback: sehr kleiner Satz
        return [
            {"key": "info_card", "version": 1, "renderer": "InfoCard", "title": "Info", "is_active": True, "schema_json": _schema_info_card()}
        ]

# ───────────────────────── Integration-Helfer ─────────────────────────

def wire_app_defaults(app) -> None:
    """
    Setzt TEMPLATE_SEED in app.config, falls weder PATH noch Seeds vorhanden.
    Idempotent.
    """
    try:
        if not app:
            return
        cfg = getattr(app, "config", {}) or {}
        has_path = bool(cfg.get("TEMPLATE_SEED_PATH"))
        has_seed = isinstance(cfg.get("TEMPLATE_SEED"), list) and len(cfg.get("TEMPLATE_SEED")) > 0
        if not has_path and not has_seed:
            app.config["TEMPLATE_SEED"] = get_default_templates()
    except Exception:
        pass


def apply_seed_to_db(app=None, overwrite: bool = False) -> int:
    """
    Materialisiert Seeds in die DB (wenn Tabellen vorhanden).
    Nutzt messages.register_template(), fällt auf 0 zurück, wenn kein App-Kontext.
    overwrite=False: existierende Keys werden nicht überschrieben.
    Rückgabe: Anzahl verarbeiteter Templates.
    """
    try:
        # App-Kontext erzwingen, falls übergeben
        if app is not None:
            try:
                ctx = app.app_context()
                ctx.push()
            except Exception:
                app = None  # weiter ohne push

        try:
            import messages as _msg  # lazy
        except Exception:
            return 0

        seeds: List[Dict[str, Any]] = []
        # 1) aus Config
        try:
            if app is not None:
                cfg_list = app.config.get("TEMPLATE_SEED")
                if isinstance(cfg_list, list):
                    seeds = [x for x in cfg_list if isinstance(x, dict)]
        except Exception:
            seeds = []
        # 2) Fallback: Defaults
        if not seeds:
            seeds = get_default_templates()

        # existierende Templates abfragen
        existing_keys = set()
        try:
            for t in _msg.list_templates() or []:
                k = str(t.get("key") or "")
                if k:
                    existing_keys.add(k)
        except Exception:
            existing_keys = set()

        written = 0
        for row in seeds:
            try:
                key = str(row.get("key") or "").strip()
                if not key:
                    continue
                if (not overwrite) and (key in existing_keys):
                    continue
                _msg.register_template(
                    key=key,
                    schema_json=row.get("schema_json") or row.get("schema") or {},
                    renderer=str(row.get("renderer") or "InfoCard"),
                    title=str(row.get("title") or key),
                    version=int(row.get("version") or 1),
                    is_active=bool(row.get("is_active", True)),
                )
                written += 1
            except Exception:
                continue
        return written
    except Exception:
        return 0
    finally:
        try:
            if app is not None:
                # Pop nur, wenn zuvor gepusht
                from flask import _app_ctx_stack  # type: ignore
                if getattr(_app_ctx_stack, "top", None) is not None:
                    try:
                        _app_ctx_stack.top.pop()
                    except Exception:
                        pass
        except Exception:
            pass


def write_seed_file(path: str) -> bool:
    """
    Schreibt die Default-Seeds als JSON-Liste an 'path'.
    """
    try:
        data = get_default_templates()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

# ───────────────────────── CLI ─────────────────────────

def _parse_args(argv: List[str]) -> Dict[str, Any]:
    opts = {"write": None, "to_db": False}
    try:
        it = iter(argv or [])
        for a in it:
            if a in ("--write", "-o"):
                try:
                    opts["write"] = next(it)
                except Exception:
                    opts["write"] = None
            elif a in ("--to-db", "--to_db"):
                opts["to_db"] = True
    except Exception:
        pass
    return opts


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    wrote = False
    if args.get("write"):
        ok = write_seed_file(args["write"])
        print(f"write_seed_file -> {'ok' if ok else 'error'}: {args['write']}")
        wrote = True
    if args.get("to_db"):
        try:
            from app import create_app  # type: ignore
            flask_app = create_app()
        except Exception:
            flask_app = None
        n = apply_seed_to_db(app=flask_app, overwrite=False)
        print(f"apply_seed_to_db -> wrote {n} templates")
        wrote = True
    if not wrote:
        try:
            print(json.dumps(get_default_templates(), ensure_ascii=False, indent=2))
        except Exception:
            # als Fallback ohne pretty-print
            print(json.dumps(get_default_templates()))
