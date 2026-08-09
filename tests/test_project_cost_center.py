from pathlib import Path
from types import SimpleNamespace

from services.project_service import (
    _ensure_project_cost_center,
    _generated_project_cost_center,
    _project_cost_center,
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
