# /services/app/src/vectoplan/viewer.py
from __future__ import annotations

"""
Viewer-URL Utilities (einheitlich mit EMBED)

Ziele:
- Einheitliche Darstellung ohne Top-Navigation.
- Linke Viewer-Toolbar sichtbar, rechte Infoleiste ausgeblendet.

Funktionen
- viewer_url(conv)                         → Model-Viewer mit #embed
- viewer_url_for(project_id, model_id)     → dito ohne Conversation
- commit_viewer_url(project_id, commit_id) → Commit-Viewer mit #embed (Fallback)

Konfiguration (optional):
- VECTOPLAN_EMBED_TOKEN: str      → als Query (?token=...)
- SPECKLE_EMBED_ENABLE: bool      → default True (immer Embed)
- SPECKLE_EMBED_HIDE_CONTROLS: bool        → default False  (linke Toolbar bleibt)
- SPECKLE_EMBED_HIDE_SELECTION_INFO: bool  → default True   (rechte Infoleiste aus)
"""

from typing import Optional

from flask import current_app
from models import Conversation

from .api import cfg


# ───────────────────────── internals ─────────────────────────

def _log(level: str, msg: str, *args) -> None:
    try:
        logger = getattr(current_app, "logger", None)
        if logger and hasattr(logger, level):
            getattr(logger, level)(msg, *args)
    except Exception:
        pass


def _q(s: str) -> str:
    try:
        import requests  # lazy
        return requests.utils.quote(s, safe="")
    except Exception:
        return s


def _as_bool(x, default: bool) -> bool:
    try:
        if isinstance(x, bool):
            return x
        if isinstance(x, (int, float)):
            return bool(x)
        if isinstance(x, str):
            v = x.strip().lower()
            if v in {"1", "true", "yes", "y", "on"}:
                return True
            if v in {"0", "false", "no", "n", "off"}:
                return False
        return default
    except Exception:
        return default


def _append_token(base: str) -> str:
    """
    Hängt ?token=... oder &token=... an, wenn konfiguriert.
    """
    try:
        tok = (current_app.config.get("VECTOPLAN_EMBED_TOKEN") or "").strip()
        if not tok:
            return base
        sep = "&" if ("?" in base) else "?"
        return f"{base}{sep}token={_q(tok)}"
    except Exception:
        return base


def _embed_fragment() -> str:
    """
    Erzeugt #embed=... gemäß Konfiguration.
    Default: enabled, hideControls=False, hideSelectionInfo=True.
    """
    try:
        enabled = _as_bool(current_app.config.get("SPECKLE_EMBED_ENABLE", True), True)
        if not enabled:
            return ""
        hide_controls = _as_bool(current_app.config.get("SPECKLE_EMBED_HIDE_CONTROLS", False), False)
        hide_sel = _as_bool(current_app.config.get("SPECKLE_EMBED_HIDE_SELECTION_INFO", True), True)

        payload = {
            "isEnabled": True,
            "hideControls": bool(hide_controls),          # linke Leiste bleibt sichtbar, daher default False
            "hideSelectionInfo": bool(hide_sel),          # rechte Infoleiste aus
        }
        import json as _json
        j = _json.dumps(payload, separators=(",", ":"))
        return f"#embed={_q(j)}"
    except Exception as ex:
        _log("warning", "embed fragment failed: %s", ex)
        # Fallback: Minimal-Embed an
        return "#embed=%7B%22isEnabled%22%3Atrue%7D"


# ───────────────────────── public URLs ─────────────────────────

def viewer_url_for(project_id: str, model_id: str) -> Optional[str]:
    """
    Model-Viewer-URL mit optionalem Token und #embed.
      https://host/projects/{projectId}/models/{modelId}[?token=...]#embed=...
    """
    try:
        if not (project_id and model_id):
            return None
        host, _ = cfg()
        base = f"{host}/projects/{_q(str(project_id))}/models/{_q(str(model_id))}"
        base = _append_token(base)
        return f"{base}{_embed_fragment()}"
    except Exception as ex:
        _log("warning", "viewer_url_for failed: %s", ex)
        return None


def commit_viewer_url(project_id: str, commit_id: str) -> Optional[str]:
    """
    Commit-Viewer-URL mit optionalem Token und #embed (nur Fallback-Fälle).
      https://host/streams/{projectId}/commits/{commitId}[?token=...]#embed=...
    """
    try:
        if not (project_id and commit_id):
            return None
        host, _ = cfg()
        base = f"{host}/streams/{_q(str(project_id))}/commits/{_q(str(commit_id))}"
        base = _append_token(base)
        return f"{base}{_embed_fragment()}"
    except Exception as ex:
        _log("warning", "commit_viewer_url failed: %s", ex)
        return None


def viewer_url(conv: Conversation) -> Optional[str]:
    """
    Wie viewer_url_for(), aber aus der Conversation gelesen.
    """
    try:
        pid = getattr(conv, "vectoplan_project_id", None)
        mid = getattr(conv, "vectoplan_model_id", None)
        if not (pid and mid):
            return None
        return viewer_url_for(str(pid), str(mid))
    except Exception as ex:
        _log("warning", "viewer_url failed: %s", ex)
        return None
