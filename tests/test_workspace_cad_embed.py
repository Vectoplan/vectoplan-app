from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from services.workspace_embed_service import WORKSPACE_CAD2D, build_workspace_embed_result


def _project(*, include_core: bool = True) -> dict:
    service_refs = {
        "chunk": {
            "status": "ready",
            "chunk_project_id": "chunk-1",
            "chunk_world_id": "world-1",
        }
    }
    if include_core:
        service_refs["core"] = {
            "status": "ready",
            "core_project_id": "core-1",
        }
    return {
        "public_id": "prj-cad-1",
        "chunk_project_id": "chunk-1",
        "chunk_world_id": "world-1",
        "chunk_status": "ready",
        "chunk_ready": True,
        "chunk_provisioning_status": "ready",
        "service_refs": service_refs,
        "access": {"role": "owner", "can_view": True},
    }


def test_cad_embed_forwards_core_reference_without_chunk_coordinates() -> None:
    result = build_workspace_embed_result(
        WORKSPACE_CAD2D,
        project_payload=_project(),
        current_user={"authenticated": True, "persistent": True},
        extra_params={"core_project_id": "untrusted", "token": "must-not-leak"},
    )

    assert result.ok is True
    query = parse_qs(urlsplit(result.url).query)
    assert query["core_project_id"] == ["core-1"]
    assert query["project_public_id"] == ["prj-cad-1"]
    assert query["workspace"] == ["cad2d"]
    assert "chunk_project_id" not in query
    assert "chunk_world_id" not in query
    assert "token" not in query


def test_cad_embed_requires_a_ready_core_reference() -> None:
    result = build_workspace_embed_result(
        WORKSPACE_CAD2D,
        project_payload=_project(include_core=False),
        current_user={"authenticated": True, "persistent": True},
    )

    assert result.ok is False
    assert result.code == "core_project_not_ready"
    assert result.status_code == 409
