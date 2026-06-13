# /services/app/routes/ui/viewer2d/__init__.py
from __future__ import annotations

from flask import Blueprint

# Der Blueprint-Name bleibt "ui_2dviewer", damit bestehende url_for(...) Aufrufe weiter funktionieren.
bp = Blueprint("ui_2dviewer", __name__)

# Routen registrieren durch Modulimporte
# (Import-Reihenfolge ist egal; beide benutzen `from . import bp`)
from . import pages as _pages  # noqa: F401
from . import cad_embed as _cad_embed  # noqa: F401
