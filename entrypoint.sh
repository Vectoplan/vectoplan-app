#!/usr/bin/env bash
set -euo pipefail

umask 022
: "${PORT:=8000}"
: "${MEDIA_ROOT:=/app/media}"

mkdir -p "$MEDIA_ROOT"

# DB warten (soft)
if [[ -n "${DATABASE_URL:-}" ]]; then
python - <<'PY'
from app import create_app          # <- app.py
from extensions import db           # <- nicht app.extensions
a = create_app()
with a.app_context():
    try:
        db.create_all()
    except Exception as ex:
        print("DB init skipped:", ex)
PY

fi

# Tabellen anlegen (idempotent)
python - <<'PY'
from app import create_app
from extensions import db
a = create_app()
with a.app_context():
    try:
        db.create_all()
    except Exception as ex:
        print("DB init skipped:", ex)
PY

# Dev-Reload optional
RELOAD=""
if [[ "${FLASK_ENV:-}" == "development" ]]; then
  RELOAD="--reload"
fi

exec gunicorn -w "${GUNICORN_WORKERS:-2}" -k gthread \
  --threads "${GUNICORN_THREADS:-4}" \
  -b "0.0.0.0:${PORT}" ${RELOAD} "wsgi:app"
