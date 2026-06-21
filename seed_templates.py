# services/app/seed_templates.py
from __future__ import annotations

"""
Seed source for message templates.

Responsibilities:
- provide default chat/card templates
- wire defaults into app.config
- optionally materialize templates into the DB through messages.register_template
- optionally write template seeds to disk

This file intentionally contains no legacy 3D viewer template seed.
The editor is embedded as a fixed iframe in the app shell, not as a chat card.
"""

import json
import sys
from typing import Any, Dict, Iterable, List, Optional


# ───────────────────────── Constants ─────────────────────────

LEGACY_TEMPLATE_KEYS = {
    "spe" + "ckle_viewer",
}

LEGACY_RENDERERS = {
    "Spe" + "ckleViewerCard",
}

DEFAULT_TEMPLATE_VERSION = 1


# ───────────────────────── Generic helpers ─────────────────────────

def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value if value is not None else default).strip()
        return text or default
    except Exception:
        return default


def _safe_int(value: Any, default: int = DEFAULT_TEMPLATE_VERSION) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = True) -> bool:
    try:
        if isinstance(value, bool):
            return value

        text = str(value if value is not None else "").strip().lower()

        if text in {"1", "true", "yes", "y", "on"}:
            return True

        if text in {"0", "false", "no", "n", "off"}:
            return False

        return default

    except Exception:
        return default


def _safe_schema(value: Any) -> Dict[str, Any]:
    try:
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _is_legacy_template(row: Any) -> bool:
    try:
        if not isinstance(row, dict):
            return False

        key = _safe_str(row.get("key"))
        renderer = _safe_str(row.get("renderer"))

        if key in LEGACY_TEMPLATE_KEYS:
            return True

        if renderer in LEGACY_RENDERERS:
            return True

        return False

    except Exception:
        return False


def _normalize_template(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize one template descriptor.

    Invalid or legacy descriptors return None.
    """
    try:
        if not isinstance(row, dict):
            return None

        if _is_legacy_template(row):
            return None

        key = _safe_str(row.get("key"))
        if not key:
            return None

        renderer = _safe_str(row.get("renderer"), "InfoCard")
        if renderer in LEGACY_RENDERERS:
            return None

        return {
            "key": key,
            "version": _safe_int(row.get("version"), DEFAULT_TEMPLATE_VERSION),
            "renderer": renderer,
            "title": _safe_str(row.get("title"), key),
            "is_active": _safe_bool(row.get("is_active"), True),
            "schema_json": _safe_schema(row.get("schema_json") or row.get("schema")),
        }

    except Exception:
        return None


def _filter_templates(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Return normalized templates without legacy viewer-card entries.
    """
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    try:
        for item in items or []:
            normalized = _normalize_template(item)

            if not normalized:
                continue

            key = normalized["key"]

            if key in seen:
                continue

            seen.add(key)
            out.append(normalized)

    except Exception:
        pass

    return out


# ───────────────────────── Schemas ─────────────────────────

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


def _schema_project_info_form() -> Dict[str, Any]:
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
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "md": {"type": "string"},
        },
        "required": ["md"],
        "additionalProperties": True,
    }


def _schema_missing_slots() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "missing": {
                "type": "array",
                "items": {"type": "string"},
            },
            "tips": {
                "type": "array",
                "items": {"type": "string"},
            },
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


# ───────────────────────── Default seeds ─────────────────────────

def get_default_templates() -> List[Dict[str, Any]]:
    """
    Return default template descriptors:

    {
      key,
      version,
      renderer,
      title,
      is_active,
      schema_json
    }
    """
    try:
        templates = [
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
                "key": "error_card",
                "version": 1,
                "renderer": "ErrorCard",
                "title": "Fehler",
                "is_active": True,
                "schema_json": _schema_error_card(),
            },
        ]

        return _filter_templates(templates)

    except Exception:
        return [
            {
                "key": "info_card",
                "version": 1,
                "renderer": "InfoCard",
                "title": "Info",
                "is_active": True,
                "schema_json": _schema_info_card(),
            }
        ]


# ───────────────────────── Integration helpers ─────────────────────────

def wire_app_defaults(app) -> None:
    """
    Set TEMPLATE_SEED in app.config if no seed path and no seed list exists.

    Idempotent and defensive.
    """
    try:
        if not app:
            return

        cfg = getattr(app, "config", {}) or {}

        has_path = bool(cfg.get("TEMPLATE_SEED_PATH"))
        existing_seed = cfg.get("TEMPLATE_SEED")
        has_seed = isinstance(existing_seed, list) and len(existing_seed) > 0

        if has_seed:
            app.config["TEMPLATE_SEED"] = _filter_templates(
                [item for item in existing_seed if isinstance(item, dict)]
            )
            return

        if not has_path:
            app.config["TEMPLATE_SEED"] = get_default_templates()

    except Exception:
        pass


def apply_seed_to_db(app=None, overwrite: bool = False) -> int:
    """
    Materialize template seeds into the DB through messages.register_template().

    overwrite=False:
      existing keys are kept.

    Returns:
      number of processed/written templates.
    """
    ctx = None

    try:
        if app is not None:
            try:
                ctx = app.app_context()
                ctx.push()
            except Exception:
                ctx = None

        try:
            import messages as msg
        except Exception:
            return 0

        seeds: List[Dict[str, Any]] = []

        try:
            if app is not None:
                cfg_list = app.config.get("TEMPLATE_SEED")
                if isinstance(cfg_list, list):
                    seeds = _filter_templates(
                        [item for item in cfg_list if isinstance(item, dict)]
                    )
        except Exception:
            seeds = []

        if not seeds:
            seeds = get_default_templates()

        existing_keys = set()

        try:
            for template in msg.list_templates() or []:
                key = _safe_str(template.get("key") if isinstance(template, dict) else "")
                if key:
                    existing_keys.add(key)
        except Exception:
            existing_keys = set()

        written = 0

        for row in _filter_templates(seeds):
            try:
                key = _safe_str(row.get("key"))

                if not key:
                    continue

                if not overwrite and key in existing_keys:
                    continue

                msg.register_template(
                    key=key,
                    schema_json=_safe_schema(row.get("schema_json") or row.get("schema")),
                    renderer=_safe_str(row.get("renderer"), "InfoCard"),
                    title=_safe_str(row.get("title"), key),
                    version=_safe_int(row.get("version"), DEFAULT_TEMPLATE_VERSION),
                    is_active=_safe_bool(row.get("is_active"), True),
                )

                written += 1
                existing_keys.add(key)

            except Exception:
                continue

        return written

    except Exception:
        return 0

    finally:
        try:
            if ctx is not None:
                ctx.pop()
        except Exception:
            pass


def write_seed_file(path: str) -> bool:
    """
    Write default seeds as JSON list to disk.
    """
    try:
        clean_path = _safe_str(path)

        if not clean_path:
            return False

        data = get_default_templates()

        with open(clean_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)

        return True

    except Exception:
        return False


# ───────────────────────── CLI ─────────────────────────

def _parse_args(argv: List[str]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "write": None,
        "to_db": False,
        "overwrite": False,
    }

    try:
        iterator = iter(argv or [])

        for arg in iterator:
            if arg in ("--write", "-o"):
                try:
                    opts["write"] = next(iterator)
                except Exception:
                    opts["write"] = None

            elif arg in ("--to-db", "--to_db"):
                opts["to_db"] = True

            elif arg == "--overwrite":
                opts["overwrite"] = True

    except Exception:
        pass

    return opts


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    wrote = False

    if args.get("write"):
        ok = write_seed_file(str(args["write"]))
        print(f"write_seed_file -> {'ok' if ok else 'error'}: {args['write']}")
        wrote = True

    if args.get("to_db"):
        try:
            from app import create_app

            flask_app = create_app()
        except Exception:
            flask_app = None

        count = apply_seed_to_db(
            app=flask_app,
            overwrite=bool(args.get("overwrite")),
        )

        print(f"apply_seed_to_db -> wrote {count} templates")
        wrote = True

    if not wrote:
        try:
            print(json.dumps(get_default_templates(), ensure_ascii=False, indent=2))
        except Exception:
            print(json.dumps(get_default_templates()))