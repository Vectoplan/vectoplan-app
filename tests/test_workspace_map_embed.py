from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from services.workspace_embed_service import _map_project_view_params


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


if __name__ == "__main__":
    unittest.main()


