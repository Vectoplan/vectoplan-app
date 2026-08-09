from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from services import core_client


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(
            {
                "ok": True,
                "created": True,
                "changed": True,
                "coreProjectId": "core-1",
                "project": {"projectId": "core-1", "status": "ready"},
            }
        ).encode("utf-8")


def test_ensure_core_project_persists_service_reference(monkeypatch):
    monkeypatch.setenv("VECTOPLAN_APP_CORE_PROVISION_ON_PROJECT_CREATE", "true")
    monkeypatch.setenv("VECTOPLAN_CORE_INTERNAL_URL", "http://vectoplan-core:5000")
    project = SimpleNamespace(
        public_id="app-1",
        name="Projekt",
        owner_user_id=7,
        auth_owner_user_id="auth-7",
        status="active",
        chunk_project_id="chunk-1",
        chunk_universe_id="universe-1",
        chunk_world_id="world-1",
        service_refs={},
        metadata_json={},
    )

    with patch.object(core_client.urllib.request, "urlopen", return_value=_Response()) as urlopen:
        result = core_client.ensure_core_project_for_app_project(project)

    assert result["ok"] is True
    assert result["coreProjectId"] == "core-1"
    assert project.service_refs["core"]["core_project_id"] == "core-1"
    assert project.service_refs["core"]["chunkProjectId"] == "chunk-1"
    request = urlopen.call_args.args[0]
    assert request.method == "PUT"
    assert request.full_url.endswith("/api/v1/projects/by-app/app-1")

