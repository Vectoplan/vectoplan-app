from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from routes.viewer import _request_extra_embed_params
from services import project_publication_service as publication_service
from services.workspace_embed_service import _clean_extra_query_params, _map_project_view_params


class WorkspaceMapEmbedTests(unittest.TestCase):
    def test_uses_current_top_level_wgs84_coordinates(self) -> None:
        params = _map_project_view_params(
            project_payload={
                "latitude": 52.520008,
                "longitude": 13.404954,
                "coordinate_srid": "EPSG:4326",
            }
        )

        self.assertEqual(
            params,
            {"lat": "52.520008", "lon": "13.404954", "zoom": "17"},
        )

    def test_uses_nested_geocoder_coordinates_and_configured_zoom(self) -> None:
        with patch.dict(os.environ, {"MAP_PROJECT_ZOOM": "18"}):
            params = _map_project_view_params(
                project_payload={
                    "coordinates": {
                        "lat": 48.137154,
                        "lng": 11.576124,
                        "srid": "EPSG:4326",
                    }
                }
            )

        self.assertEqual(
            params,
            {"lat": "48.137154", "lon": "11.576124", "zoom": "18"},
        )

    def test_address_change_produces_a_new_start_view(self) -> None:
        first = _map_project_view_params(
            project_payload={"latitude": 52.520008, "longitude": 13.404954}
        )
        changed = _map_project_view_params(
            project_payload={"latitude": 53.551086, "longitude": 9.993682}
        )

        self.assertNotEqual(first, changed)
        self.assertEqual(changed["lat"], "53.551086")
        self.assertEqual(changed["lon"], "9.993682")

    def test_rejects_non_wgs84_and_invalid_coordinates(self) -> None:
        self.assertEqual(
            _map_project_view_params(
                project_payload={
                    "latitude": 6894699.8,
                    "longitude": 1492237.8,
                    "coordinate_srid": "EPSG:3857",
                }
            ),
            {},
        )
        self.assertEqual(
            _map_project_view_params(
                project_payload={"latitude": 91, "longitude": 13.4}
            ),
            {},
        )


class WorkspacePresentationQueryTests(unittest.TestCase):
    def test_initial_panel_is_forwarded_without_security_parameters(self) -> None:
        app = Flask(__name__)

        with app.test_request_context("/?mode=preview&initial_panel=none&token=must-not-leak"):
            self.assertEqual(
                _request_extra_embed_params(),
                {"mode": "preview", "initial_panel": "none"},
            )

        self.assertEqual(
            _clean_extra_query_params(
                {"mode": "preview", "initial_panel": "none", "token": "must-not-leak"}
            ),
            {"mode": "preview", "initial_panel": "none"},
        )


class ProjectPublicationDefaultsTests(unittest.TestCase):
    def test_public_visibility_selects_every_publication_workspace(self) -> None:
        project = SimpleNamespace(public_id="prj_public_defaults", is_demo=False)
        update_publication = Mock(return_value="updated")

        with (
            patch.object(publication_service, "resolve_project", return_value=project),
            patch.object(publication_service, "_project_is_demo", return_value=False),
            patch.object(
                publication_service,
                "get_or_create_publication_policy",
                return_value=object(),
            ),
            patch.object(
                publication_service,
                "_desired_workspaces_from_policy",
                return_value={"project": True, "map": False},
            ),
        ):
            service = publication_service.ProjectPublicationService()
            service.update_publication = update_publication

            result = service.set_visibility(
                project,
                "public",
                actor_user_id=7,
                commit=False,
            )

        self.assertEqual(result, "updated")
        payload = update_publication.call_args.kwargs["data"]
        self.assertEqual(payload["visibility"], "public")
        self.assertEqual(
            payload["published_workspaces"],
            {
                workspace: True
                for workspace in publication_service.PUBLICATION_WORKSPACES
            },
        )

if __name__ == "__main__":
    unittest.main()


