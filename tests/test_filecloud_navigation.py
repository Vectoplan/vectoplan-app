from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from flask import Flask, Response

from routes.viewer import bp
from routes.ui import projects as project_shell_routes
from services import project_publication_service, project_workspace_context


def _app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        VECTOPLAN_FILECLOUD_EMBED_ENABLED=True,
        VECTOPLAN_FILECLOUD_PUBLIC_URL="http://localhost:5107",
        VECTOPLAN_FILECLOUD_ROUTE="/files",
    )
    app.register_blueprint(bp)
    return app


def _project_context() -> dict:
    return {
        "project": {
            "public_id": "prj_alpha_12345678",
            "name": "Alpha",
            "configured": True,
        }
    }


def test_files_route_uses_public_project_id_and_filecloud_port() -> None:
    app = _app()
    with patch("routes.viewer._configured_project_context", return_value=(_project_context(), None)):
        response = app.test_client().get("/ui/project/prj_alpha_12345678/files")

    assert response.status_code == 302
    assert response.headers["Location"] == "http://localhost:5107/files?project_id=prj_alpha_12345678"


def test_calculation_routes_have_project_scoped_shells() -> None:
    app = _app()
    cases = {
        "structural-calculation": "Tragwerksberechnung",
        "energy-calculation": "Energieberechnung",
        "sound-protection-calculation": "Schallschutzberechnung",
    }
    with patch("routes.viewer._configured_project_context", return_value=(_project_context(), None)):
        client = app.test_client()
        for suffix, label in cases.items():
            response = client.get(f"/ui/project/prj_alpha_12345678/{suffix}")
            assert response.status_code == 200
            assert label in response.get_data(as_text=True)


def test_side_menu_replaces_versions_and_admin() -> None:
    source = (Path(__file__).parents[1] / "templates" / "chat_viewer.html").read_text(encoding="utf-8")

    for control_id in (
        "modeFilesBtn",
        "modeStructuralCalculationBtn",
        "modeEnergyCalculationBtn",
        "modeSoundProtectionCalculationBtn",
    ):
        assert f'id="{control_id}"' in source
    assert 'id="versionsToggleBtn"' not in source
    assert 'id="modeAdminBtn"' not in source
    assert "versions_published_raw" not in source
    assert "structural_calculation_published_raw" in source
    assert "energy_calculation_published_raw" in source
    assert "sound_protection_calculation_published_raw" in source


def test_publication_workspaces_replace_versions_with_project_services() -> None:
    expected_new = {
        "files",
        "structural_calculation",
        "energy_calculation",
        "sound_protection_calculation",
    }

    assert expected_new.issubset(set(project_publication_service.PUBLICATION_WORKSPACES))
    assert expected_new.issubset(set(project_workspace_context.PUBLICATION_WORKSPACES))
    assert "versions" not in project_publication_service.PUBLICATION_WORKSPACES
    assert "versions" not in project_workspace_context.PUBLICATION_WORKSPACES

    source = (
        Path(__file__).parents[1]
        / "templates"
        / "viewer"
        / "partials"
        / "project_publication.html"
    ).read_text(encoding="utf-8")
    for workspace in expected_new:
        assert f"'key': '{workspace}'" in source
    assert "'key': 'versions'" not in source


def test_workspace_navigation_has_bookmarkable_project_suffixes() -> None:
    root = Path(__file__).parents[1]
    routes_source = (root / "routes" / "ui" / "projects.py").read_text(encoding="utf-8")
    shell_source = (root / "static" / "js" / "chat" / "main.js").read_text(encoding="utf-8")

    assert '@bp.get("/project=<project_id>/<workspace>")' in routes_source
    for suffix in (
        "files",
        "structural-calculation",
        "energy-calculation",
        "sound-protection-calculation",
    ):
        assert f'"{suffix}"' in shell_source
    assert "window.history.pushState" in shell_source


def test_direct_workspace_suffix_opens_shell_in_requested_mode() -> None:
    app = Flask(__name__)
    app.register_blueprint(project_shell_routes.bp)
    project = object()

    with (
        patch.object(project_shell_routes, "_current_user_payload", return_value={}),
        patch.object(project_shell_routes, "_is_access_blocked_context", return_value=False),
        patch.object(project_shell_routes, "_load_selected_project", return_value=project),
        patch.object(
            project_shell_routes,
            "_render_project_shell",
            return_value=Response("energy_calculation", status=200),
        ) as render_shell,
    ):
        response = app.test_client().get(
            "/project=prj_alpha_12345678/energy-calculation"
        )

    assert response.status_code == 200
    render_shell.assert_called_once_with(
        selected_project=project,
        is_new=False,
        initial_workspace="energy_calculation",
    )
