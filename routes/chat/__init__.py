# /services/app/routes/chat/__init__.py
from __future__ import annotations

"""
Chat API (modularisiert)

Blueprint: bp = "chat_api"

Untermodule:
- helpers.py   → Konstanten, Utils, Upload/Versioning, Viewer-Resolver, ChatAI-Hilfen
- crud.py      → /v1/chats (POST, GET)
- sync.py      → /v1/chat (POST)
- stream.py    → /v1/chat/stream (POST, SSE)

Import-Reihenfolge ist wichtig: zuerst Blueprint, dann Routenmodule importieren.
"""

from flask import Blueprint

bp = Blueprint("chat_api", __name__)

# Routen registrieren (Side-Effect-Importe)
from . import crud  # noqa: E402,F401
from . import sync  # noqa: E402,F401
from . import stream  # noqa: E402,F401
