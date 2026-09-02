from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask

from routes import projects_api
from services import project_service


class ProjectGeocodingFallbackTests(unittest.TestCase):
    def test_suggest_route_removes_mapbox_attribution_for_fallback(self) -> None:
        item = {
            "id": "fallback:berlin-default",
            "source": "default_berlin",
            "provider": "fallback/default_berlin",
            "is_fallback": True,
            "longitude": 13.405,
            "latitude": 52.52,
        }
        app = Flask(__name__)
        with (
            app.test_request_context("/v1/geocoding/suggest?q=Berlin&limit=5"),
            patch.object(projects_api, "_require_persistent_context", return_value=None),
            patch.object(projects_api, "search_addresses", return_value=[item]),
            patch.object(
                projects_api,
                "geocoding_status",
                return_value={"fallback_available": True},
            ),
        ):
            response_result = projects_api.geocoding_suggest()

        response, status_code = response_result
        payload = response.get_json()
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["provider"], "default_berlin")
        self.assertIsNone(payload["attribution"])
        self.assertIs(payload["fallback_used"], True)

    def test_mapbox_selection_mismatch_is_allowed_only_for_marked_berlin_fallback(self) -> None:
        fallback_result = {
            "id": "fallback:berlin-default",
            "mapbox_id": "fallback:berlin-default",
            "label": "Berlin, Deutschland (Standardstandort)",
            "address_text": "Berlin, Deutschland (Standardstandort)",
            "longitude": 13.405,
            "latitude": 52.52,
            "x": 13.405,
            "y": 52.52,
            "coordinate_srid": "EPSG:4326",
            "source_crs_id": "EPSG:4326",
            "provider": "fallback/default_berlin",
            "source": "default_berlin",
            "is_fallback": True,
        }
        payload = {
            "address_text": "Alexanderplatz 1, Berlin",
            "address_mapbox_id": "mapbox.address.real-selection",
            "__present": {"address_text", "address_mapbox_id"},
        }

        with (
            patch.object(
                project_service,
                "mapbox_geocoding_status",
                return_value={"fallback_available": True},
            ),
            patch.object(
                project_service,
                "mapbox_geocode_address",
                return_value={
                    "ok": True,
                    "provider": "fallback/default_berlin",
                    "result": fallback_result,
                    "results": [fallback_result],
                },
            ),
        ):
            enriched = project_service._enrich_project_payload_with_mapbox(payload)

        self.assertEqual(enriched["latitude"], 52.52)
        self.assertEqual(enriched["longitude"], 13.405)
        self.assertEqual(enriched["geocode_payload"]["source"], "default_berlin")


if __name__ == "__main__":
    unittest.main()
