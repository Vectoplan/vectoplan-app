from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services import project_service
from services.project_service import (
    _ensure_project_cost_center,
    _generated_project_cost_center,
    _merge_project_settings,
    _normalize_project_payload,
    _project_cost_center,
    serialize_project,
)


APP_ROOT = Path(__file__).resolve().parents[1]


def _project(**overrides):
    values = {
        "public_id": "prj_df6956febf9a419789207530",
        "name": "Test 1",
        "settings": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_project_cost_center_is_generated_stably_and_persisted():
    project = _project()

    generated = _generated_project_cost_center(project)

    assert generated == "KST-TEST-1-89207530"
    assert _ensure_project_cost_center(project) == generated
    assert project.settings["cost_center"] == generated


def test_manual_project_cost_center_wins_over_generated_value():
    project = _project(settings={"cost_center": "KST-SCHILLERBRUECKE"})

    assert _project_cost_center(project) == "KST-SCHILLERBRUECKE"
    assert _ensure_project_cost_center(project) == "KST-SCHILLERBRUECKE"


def test_browser_project_copy_keeps_cost_center():
    script = (APP_ROOT / "static" / "js" / "project" / "project_form.js").read_text(
        encoding="utf-8"
    )

    assert 'cost_center: p.cost_center || p.costCenter || ""' in script
    assert 'costCenter: p.costCenter || p.cost_center || ""' in script


def test_project_settings_merge_keeps_unknown_dashboard_siblings():
    merged = _merge_project_settings(
        {
            "cost_center": "KST-ALT",
            "dashboard": {
                "currency": "EUR",
                "start_date": "2026-08-01",
                "budget_total": 100,
            },
            "publication": {"editor3d": True},
        },
        {"dashboard": {"budget_total": "250", "completion_percent": "30"}},
    )

    assert merged["dashboard"] == {
        "currency": "EUR",
        "start_date": "2026-08-01",
        "budget_total": "250",
        "completion_percent": "30",
    }
    assert merged["publication"] == {"editor3d": True}


def test_dashboard_update_is_normalized_and_preserves_existing_settings():
    project = _project(
        settings={
            "cost_center": "KST-ALT",
            "dashboard": {"currency": "EUR", "budget_total": 100},
            "publication": {"editor3d": True},
        }
    )

    payload = _normalize_project_payload(
        {
            "settings": {
                "dashboard": {
                    "budgetTotal": "250,5",
                    "completion_percent": "30",
                }
            }
        },
        for_update=True,
        existing_project=project,
    )

    assert "settings" in payload["__present"]
    assert payload["settings"]["dashboard"] == {
        "currency": "EUR",
        "budget_total": 250.5,
        "completion_percent": 30,
    }
    assert payload["settings"]["publication"] == {"editor3d": True}


def test_private_settings_serialization_requires_view_settings_permission():
    include_private_calls = []
    project = _project(
        id=42,
        settings={"cost_center": "KST-SECRET", "dashboard": {"budget_total": 100}},
        visibility="private",
    )

    def to_dict(**kwargs):
        include_private_calls.append(kwargs.get("include_private"))
        return {
            "public_id": project.public_id,
            "name": project.name,
            "settings": project.settings,
            "cost_center": "KST-SECRET",
        }

    project.to_dict = to_dict
    with patch(
        "services.project_service.get_project_permission_result",
        return_value=SimpleNamespace(can_view_settings=False),
    ):
        hidden = serialize_project(
            project,
            user_id=7,
            include_permissions=False,
            include_publication=False,
        )

    assert include_private_calls[-1] is False
    assert "settings" not in hidden
    assert "cost_center" not in hidden

    with patch(
        "services.project_service.get_project_permission_result",
        return_value=SimpleNamespace(can_view_settings=True),
    ):
        visible = serialize_project(
            project,
            user_id=7,
            include_permissions=False,
            include_publication=False,
        )

    assert include_private_calls[-1] is True
    assert visible["settings"]["dashboard"]["budget_total"] == 100
    assert visible["cost_center"] == "KST-SECRET"


def test_create_result_uses_the_deferred_external_provisioning_fast_path():
    project = _project(
        id=42,
        service_refs={},
        _last_chunk_operation_result=project_service.ProjectOperationResult(
            ok=True,
            payload={"ok": True, "deferred": True},
            status_code=202,
            code="chunk_provisioning_deferred",
        ),
        _last_core_operation_result={
            "ok": True,
            "status": "pending",
            "deferred": True,
            "code": "core_provisioning_deferred",
        },
    )

    with (
        patch("services.project_service.get_actor_context", return_value={"demo_mode": False}),
        patch("services.project_service.create_project", return_value=project) as create,
        patch(
            "services.project_service._project_chunk_refs",
            return_value={"provisioning_status": "pending", "ready": False},
        ),
        patch("services.project_service._chunk_provisioning_required", return_value=False),
        patch("services.project_service._core_provisioning_required", return_value=False),
        patch("services.project_service.serialize_project", return_value={"public_id": project.public_id}),
        patch(
            "services.project_service.serialize_project_sidebar_item",
            return_value={"public_id": project.public_id},
        ),
        patch(
            "services.project_service.project_public_url",
            return_value=f"/project={project.public_id}",
        ),
    ):
        result = project_service.create_project_result(
            {"name": "Schnelles Projekt"},
            user_id=7,
        )

    assert result.ok is True
    assert result.status_code == 201
    assert result.code == "project_created_chunk_pending"
    assert result.payload["external_provisioning_deferred"] is True
    assert result.payload["chunk_operation"]["code"] == "chunk_provisioning_deferred"
    assert create.call_args.kwargs["defer_external_provisioning"] is True
