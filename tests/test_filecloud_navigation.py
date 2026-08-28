from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask, Response

from routes.viewer import bp
from routes.ui import projects as project_shell_routes
from services import project_publication_service, project_workspace_context


def _app() -> Flask:
    root = Path(__file__).parents[1]
    app = Flask(
        __name__,
        template_folder=str(root / "templates"),
        static_folder=str(root / "static"),
        static_url_path="/static",
    )
    app.config.update(
        TESTING=True,
        VECTOPLAN_FILECLOUD_EMBED_ENABLED=True,
        VECTOPLAN_FILECLOUD_PUBLIC_URL="http://localhost:5107",
        VECTOPLAN_FILECLOUD_ROUTE="/files",
        VECTOPLAN_FILECLOUD_ACCESS_TICKET_SECRET="test-filecloud-access-ticket-secret-32-bytes-minimum",
    )
    app.register_blueprint(bp)
    return app


def _project_context() -> dict:
    return {
        "project": {
            "public_id": "prj_alpha_12345678",
            "name": "Alpha",
            "configured": True,
            "access": {"role": "owner", "can_view": True, "can_edit": True, "can_manage": True},
        },
        "current_user": {
            "authenticated": True,
            "auth_user_id": "auth_user_alpha",
            "email": "owner@example.com",
        },
        "workspace_access": {
            "allowed": True,
            "can_view": True,
            "can_edit": True,
            "can_manage": True,
            "project_role": "owner",
            "access_mode": "authenticated",
        },
    }


def test_files_route_uses_public_project_id_and_filecloud_port() -> None:
    app = _app()
    with patch("routes.viewer._configured_project_context", return_value=(_project_context(), None)):
        response = app.test_client().get("/ui/project/prj_alpha_12345678/files")

    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    query = parse_qs(location.query)
    assert (location.scheme, location.netloc, location.path) == ("http", "localhost:5107", "/files")
    assert query["project_id"] == ["prj_alpha_12345678"]
    assert len(query["vp_access_ticket"][0].split(".")) == 2


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

            if suffix == "structural-calculation":
                assert 'id="statik-app"' in response.get_data(as_text=True)
                assert 'class="mobile-panel-button explorer-button"' in response.get_data(as_text=True)
                assert response.headers["X-VECTOPLAN-Preview"] == "display-only"
            elif suffix == "energy-calculation":
                html = response.get_data(as_text=True)
                assert 'id="energy-app"' in html
                assert '<nav class="module-rail"' in html
                assert '<aside class="module-rail"' not in html
                assert "project-tabs-bar" not in html
                assert "Projektziel festlegen" in html
                assert 'data-step="1"' in html
                assert 'id="funding-program-grid"' in html
                assert 'id="funding-units"' in html
                assert 'id="funding-income"' in html
                assert 'id="funding-children"' in html
                assert 'id="system-flow-stage"' in html
                assert 'id="system-heat-source"' in html
                assert 'id="system-floor-heating"' in html
                assert 'class="flow-particle electric particle-pv"' in html
                assert 'id="planner-floor-heating-visual"' in html
                assert 'id="balance-thermal-bridge"' in html
                assert 'data-isfp-step' in html
                assert 'id="pipeline-strip"' not in html
                assert "Projektmodell normalisieren" not in html
                assert 'class="diagram-controls"' in html
                assert 'class="certificate-workspace"' in html
                assert 'id="certificate-preview-final"' in html
                assert 'data-certificate-preview-page="1"' in html
                assert 'data-certificate-preview-page="2"' in html
                assert "GEG-Muster 2024" in html
                assert "Projektmodell · 2D" not in html
                assert "Projektmodell · 3D" not in html
                assert "Ziel übernehmen" not in html
                assert response.headers["X-VECTOPLAN-Preview"] == "display-only"


def test_calculation_preview_assets_are_served_locally() -> None:
    app = _app()
    client = app.test_client()

    for path in (
        "/static/statik/css/main.css",
        "/static/statik/js/main.js",
        "/static/statik/examples/sample_model.json",
        "/static/energie/css/main.css",
        "/static/energie/js/main.js",
        "/static/energie/examples/sample_project.json",
        "/static/energie/examples/display_pipeline.json",
    ):
        response = client.get(path)
        assert response.status_code == 200, path


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
        "einstellungen", "map", "3d", "2d", "lv",
        "file", "statik", "energie", "schallschutz",
    ):
        assert f'"{suffix}"' in shell_source
    assert "window.history.pushState" in shell_source


@pytest.mark.parametrize(("suffix", "mode"), [
    ("einstellungen", "project"), ("project", "project"),
    ("map", "map"), ("3d", "3d"), ("2d", "2d"), ("lv", "lv"),
    ("file", "files"), ("files", "files"), ("dateien", "files"),
    ("statik", "structural_calculation"),
    ("structural-calculation", "structural_calculation"),
    ("structural_calculation", "structural_calculation"),
    ("energie", "energy_calculation"),
    ("energy-calculation", "energy_calculation"),
    ("energy_calculation", "energy_calculation"),
    ("schallschutz", "sound_protection_calculation"),
    ("sound-protection-calculation", "sound_protection_calculation"),
    ("sound_protection_calculation", "sound_protection_calculation"),
])
def test_direct_workspace_suffix_opens_shell_in_requested_mode(suffix: str, mode: str) -> None:
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
            return_value=Response(mode, status=200),
        ) as render_shell,
    ):
        response = app.test_client().get(
            f"/project=prj_alpha_12345678/{suffix}"
        )

    assert response.status_code == 200
    render_shell.assert_called_once_with(
        selected_project=project,
        is_new=False,
        initial_workspace=mode,
    )


def test_unknown_workspace_suffix_is_rejected() -> None:
    app = Flask(__name__)
    app.register_blueprint(project_shell_routes.bp)
    response = app.test_client().get("/project=prj_alpha_12345678/not-a-workspace")
    assert response.status_code == 404


def test_workspace_csp_allows_nested_vergabe_without_wildcard_origins():
    app = _app()
    with app.test_request_context('/project=prj_alpha_12345678?allow_embed=1'):
        policy = project_shell_routes._workspace_csp_header_value()
        assert 'http://localhost:5203' in policy and 'http://127.0.0.1:5203' in policy
        assert 'http://localhost:*' not in policy
        app.config['VECTOPLAN_VERGABE_PUBLIC_URL'] = 'https://vergabe.example/vergabe'
        policy = project_shell_routes._workspace_csp_header_value()
        assert 'https://vergabe.example;' in policy or 'https://vergabe.example ' in policy
        assert 'https://vergabe.example/vergabe' not in policy


def test_direct_workspace_suffix_keeps_access_checks() -> None:
    app = Flask(__name__)
    app.register_blueprint(project_shell_routes.bp)
    with (
        patch.object(project_shell_routes, "_current_user_payload", return_value={}),
        patch.object(project_shell_routes, "_is_access_blocked_context", return_value=True),
        patch.object(project_shell_routes, "_blocked_response", return_value=Response(status=403)),
        patch.object(project_shell_routes, "_render_project_shell") as render_shell,
    ):
        response = app.test_client().get("/project=prj_alpha_12345678/file")
    assert response.status_code == 403
    render_shell.assert_not_called()
