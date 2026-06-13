# /services/app/vectoplan.py
from __future__ import annotations
# Kompatibilitätslayer: re-exportiert das neue Paket
from src.vectoplan import (  # type: ignore
    ensure_and_refresh,
    ensure_model,
    ensure_placeholder_if_empty,
    ensure_bootstrap_viewer,
    upload_file_to_project,
    viewer_url,
    _cfg as _cfg,  # optional
)
