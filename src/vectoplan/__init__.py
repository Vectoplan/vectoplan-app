# /services/app/src/vectoplan/__init__.py
from __future__ import annotations

"""
VectoPlan (Speckle) – Public API.

Dieses Paket ersetzt die frühere 'vectoplan.py'.
Öffentliche Funktionen (re-exported):

- ensure_and_refresh(conv)         → Projekt/Modelle cachen oder anlegen
- ensure_model(project_id, ...)    → Model-ID sicherstellen
- ensure_placeholder_if_empty(conv)→ beim leeren Projekt ein Modell + Platzhalter-Commit anlegen
- ensure_bootstrap_viewer(conv)    → *bequeme* Variante: stellt sicher, dass der Viewer immer etwas zeigt
- upload_file_to_project(conv, blob, ...) → Datei als neue Version an Modell anhängen
- viewer_url(conv)                 → IMMER Model-Viewer-URL (ohne embed → linke Menüleiste sichtbar)

Alle Funktionen sind robust gegen Exceptions und loggen über Flask's current_app.logger.
"""

from typing import Any, Dict, Optional, Tuple

# ───────────────────────── internals ─────────────────────────

def _log(level: str, msg: str, *args) -> None:
    try:
        from flask import current_app  # lazy
        logger = getattr(current_app, "logger", None)
        if logger and hasattr(logger, level):
            getattr(logger, level)(msg, *args)
    except Exception:
        pass


# ───────────────────────── re-exports (lazy) ─────────────────────────

def ensure_and_refresh(conv) -> None:
    """Siehe projects.ensure_and_refresh"""
    try:
        from .projects import ensure_and_refresh as _fn
        return _fn(conv)
    except Exception as ex:
        _log("warning", "vectoplan.ensure_and_refresh failed: %s", ex)


def ensure_model(project_id: str, preferred_name: Optional[str] = None) -> Optional[str]:
    """Siehe projects.ensure_model"""
    try:
        from .projects import ensure_model as _fn
        return _fn(project_id, preferred_name)
    except Exception as ex:
        _log("warning", "vectoplan.ensure_model failed: %s", ex)
        return None


def viewer_url(conv) -> Optional[str]:
    """IMMER Model-Viewer-URL (ohne embed, linke Menüleiste sichtbar)."""
    try:
        from .viewer import viewer_url as _fn
        return _fn(conv)
    except Exception as ex:
        _log("warning", "vectoplan.viewer_url failed: %s", ex)
        return None


def ensure_placeholder_if_empty(conv) -> Optional[str]:
    """
    Falls noch kein Modell existiert: Modell anlegen, Platzhalter-Commit schreiben,
    conv.vectoplan_model_id aktualisieren. Rückgabe: Model-Viewer-URL oder None.
    """
    try:
        from .placeholder import ensure_placeholder_if_empty as _fn
        return _fn(conv)
    except Exception as ex:
        _log("warning", "vectoplan.ensure_placeholder_if_empty failed: %s", ex)
        return None


def ensure_bootstrap_viewer(conv) -> Optional[str]:
    """
    Bequeme Variante für den ersten Seitenaufruf:

    1) ensure_and_refresh(conv)
    2) falls weiterhin kein Modell → ensure_placeholder_if_empty(conv)
    3) viewer_url(conv) zurückgeben

    Ziel: Der Viewer zeigt *immer* etwas, ohne dass der Aufrufer
    die Interna von Projekten/Modellen kennen muss.
    """
    try:
        ensure_and_refresh(conv)
        # Wenn noch kein Modell cachen konnte → Platzhalter anlegen
        if not getattr(conv, "vectoplan_model_id", None):
            try:
                _ = ensure_placeholder_if_empty(conv)
            except Exception:
                pass
        return viewer_url(conv) or None
    except Exception as ex:
        _log("warning", "vectoplan.ensure_bootstrap_viewer failed: %s", ex)
        return None


def upload_file_to_project(conv, blob, model_name: Optional[str] = None, file_ext: Optional[str] = None) -> Dict[str, Any]:
    """Siehe upload.upload_file_to_project"""
    try:
        from .upload import upload_file_to_project as _fn
        return _fn(conv, blob, model_name=model_name, file_ext=file_ext) or {}
    except Exception as ex:
        _log("warning", "vectoplan.upload_file_to_project failed: %s", ex)
        return {}


# ───────────────────────── compat helpers ─────────────────────────

def _cfg() -> Tuple[str, Optional[str]]:
    """Re-export der Host/Token-Auflösung (für seltene Sonderfälle)."""
    try:
        from .api import cfg as _cfg_fn  # type: ignore[attr-defined]
        return _cfg_fn()
    except Exception:
        # Fallback, sollte selten gebraucht werden
        return "https://vectoplan.com", None
