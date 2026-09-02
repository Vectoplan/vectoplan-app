from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from routes import projects_api as routes
from services import project_service


def _project(**overrides):
    values = {
        "public_id": "prj_alpha_12345678",
        "name": "Schulneubau",
        "settings": {
            "cost_center": "KST-4711",
            "dashboard": {"budget_total": 2_000_000},
        },
        "address_text": "Musterstraße 1, 10115 Berlin",
        "street": "Musterstraße",
        "house_number": "1",
        "postal_code": "10115",
        "city": "Berlin",
        "region": "Berlin",
        "country": "Deutschland",
        "is_demo": False,
        "project_scope": "personal",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _access(**overrides):
    values = {
        "role": "editor",
        "authenticated": True,
        "persistent": True,
        "is_member": True,
        "is_public_viewer": False,
        "is_unlisted_viewer": False,
        "demo_mode": False,
        "blocked": False,
        "can_view": True,
        "can_edit": True,
        "can_manage": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_member_projection_exposes_cost_center_without_private_settings(monkeypatch):
    monkeypatch.setattr(
        project_service,
        "permissions_get_project_permission_result",
        lambda *args, **kwargs: _access(),
    )

    item = project_service.serialize_filecloud_project(_project(), user_id=7)

    assert set(item) == {
        "public_id",
        "name",
        "cost_center",
        "city",
        "address",
        "permissions",
    }
    assert item["cost_center"] == "KST-4711"
    assert item["city"] == "Berlin"
    assert item["address"] == {
        "text": "Musterstraße 1, 10115 Berlin",
        "city": "Berlin",
    }
    assert item["permissions"] == {
        "role": "editor",
        "can_view": True,
        "can_edit": True,
        "can_manage": False,
        "read_only": False,
    }
    assert "settings" not in item
    assert "dashboard" not in item


@pytest.mark.parametrize(
    "access",
    [
        _access(is_member=False, is_public_viewer=True, can_edit=False),
        _access(is_member=False, source="account"),
        _access(persistent=False, demo_mode=True),
        _access(can_view=False),
        _access(role="guest"),
    ],
)
def test_projection_rejects_non_member_public_demo_and_invalid_access(monkeypatch, access):
    monkeypatch.setattr(
        project_service,
        "permissions_get_project_permission_result",
        lambda *args, **kwargs: access,
    )

    assert project_service.serialize_filecloud_project(_project(), user_id=7) == {}


def test_catalog_query_does_not_use_account_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(
        project_service,
        "get_actor_context",
        lambda *args, **kwargs: {
            "authenticated": True,
            "persistent": True,
            "demo_mode": False,
            "blocked": False,
        },
    )
    monkeypatch.setattr(project_service, "get_actor_user_id_optional", lambda *args: 7)

    def list_projects(**kwargs):
        calls.append(kwargs)
        return [_project()]

    monkeypatch.setattr(project_service, "list_projects_for_user", list_projects)
    monkeypatch.setattr(
        project_service,
        "serialize_filecloud_project",
        lambda *args, **kwargs: {"public_id": "prj_alpha_12345678"},
    )

    result = project_service.list_filecloud_projects_result(user_id=7)

    assert result.ok is True
    assert calls[0]["include_account_projects"] is False
    assert calls[0]["include_public"] is False
    assert calls[0]["include_unlisted"] is False
    assert calls[0]["include_demo"] is False
    assert result.payload["next_offset"] is None


@pytest.fixture()
def catalog_client(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(routes.bp)
    monkeypatch.setattr(routes, "_require_persistent_context", lambda: None)
    monkeypatch.setattr(routes, "_current_user_id_optional", lambda: 7)
    monkeypatch.setattr(
        routes,
        "list_filecloud_projects_result",
        lambda **kwargs: project_service.ProjectOperationResult(
            ok=True,
            payload={
                "ok": True,
                "items": [{"public_id": "prj_alpha_12345678"}],
                "projects": [{"public_id": "prj_alpha_12345678"}],
                "total": 1,
                "limit": kwargs["limit"],
                "offset": kwargs["offset"],
                "next_offset": None,
            },
            status_code=200,
            code="filecloud_projects_loaded",
        ),
    )
    return app.test_client()


def test_catalog_route_is_no_store_and_uses_bounded_pagination(catalog_client):
    response = catalog_client.get("/v1/projects/filecloud-catalog?limit=500&offset=0")

    assert response.status_code == 200
    assert response.json["items"] == [{"public_id": "prj_alpha_12345678"}]
    assert response.json["next_offset"] is None
    assert "no-store" in response.headers["Cache-Control"]


def test_catalog_route_rejects_non_persistent_identity(catalog_client, monkeypatch):
    monkeypatch.setattr(
        routes,
        "_require_persistent_context",
        lambda: routes._json_error(
            "Persistenter authentifizierter User erforderlich.",
            403,
            code="persistent_user_required",
        ),
    )

    response = catalog_client.get("/v1/projects/filecloud-catalog")

    assert response.status_code == 403
    assert response.json["code"] == "persistent_user_required"
    assert response.json.get("items") is None
