# /services/app/extensions.py
from __future__ import annotations
import logging
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def init_logging(level: int = logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
