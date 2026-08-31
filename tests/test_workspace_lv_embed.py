from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from services.workspace_embed_service import (
    WORKSPACE_LV,
    build_workspace_embed_result,
    get_workspace_target_config,
    is_external_workspace,
)


def _owner_project() -> dict:
    return {
        "public_id": "prj_demo_lv",
        "access": {
            "role": "owner",
            "can_view": True,
        },
    }


def test_lv_is_an_external_workspace_with_local_default_target() -> None:
    target = get_workspace_target_config(WORKSPACE_LV)

    assert is_external_workspace(WORKSPACE_LV) is True
    assert target.enabled is True
    assert target.service_name == "vectoplan-lv"
    assert target.public_route_url == "http://localhost:5105/lv"


def test_lv_embed_forwards_only_the_public_project_key() -> None:
    result = build_workspace_embed_result(
        WORKSPACE_LV,
        project_payload=_owner_project(),
        current_user={"authenticated": True},
        extra_params={"theme": "dark", "token": "must-not-leak"},
    )

    assert result.ok is True
    assert result.params == {"project_public_id": "prj_demo_lv"}
    parsed = urlsplit(result.url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "http://localhost:5105/lv"
    assert parse_qs(parsed.query) == {"project_public_id": ["prj_demo_lv"]}


def test_lv_embed_forwards_only_the_explicit_desktop_embed_marker() -> None:
    result = build_workspace_embed_result(
        WORKSPACE_LV,
        project_payload=_owner_project(),
        current_user={"authenticated": True},
        extra_params={
            "allow_embed": "1",
            "client_source": "desktop",
            "token": "must-not-leak",
        },
    )

    assert result.ok is True
    assert parse_qs(urlsplit(result.url).query) == {
        "project_public_id": ["prj_demo_lv"],
        "allow_embed": ["1"],
        "client_source": ["desktop"],
    }
