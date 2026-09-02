from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from services import geocoding_client


TOKEN_ENV_NAMES = tuple(geocoding_client.TOKEN_ENV_NAMES)


def _token_environment(**values: str) -> dict[str, str]:
    environment = {
        name: ""
        for name in (
            *TOKEN_ENV_NAMES,
            "VECTOPLAN_APP_GEOCODING_FALLBACK_ENABLED",
            "VECTOPLAN_APP_GEOCODING_FALLBACK_LONGITUDE",
            "VECTOPLAN_APP_GEOCODING_FALLBACK_LATITUDE",
            "VECTOPLAN_APP_GEOCODING_FALLBACK_LABEL",
        )
    }
    environment.update(values)
    return environment


class GeocodingClientTests(unittest.TestCase):
    def assert_default_berlin(
        self,
        item: dict[str, object],
        *,
        reason: str,
    ) -> None:
        self.assertEqual(item["id"], "fallback:berlin-default")
        self.assertEqual(item["mapbox_id"], "fallback:berlin-default")
        self.assertEqual(item["provider"], "fallback/default_berlin")
        self.assertEqual(item["source"], "default_berlin")
        self.assertIs(item["is_fallback"], True)
        self.assertEqual(item["fallback_reason"], reason)
        self.assertEqual(item["longitude"], 13.4050)
        self.assertEqual(item["latitude"], 52.5200)
        self.assertEqual(item["coordinate_srid"], "EPSG:4326")

    def test_mapbox_token_aliases_are_supported(self) -> None:
        for name in TOKEN_ENV_NAMES:
            with self.subTest(name=name), patch.dict(
                os.environ,
                _token_environment(**{name: "pk.alias-token-for-test"}),
            ):
                self.assertEqual(
                    geocoding_client._configured_token(),
                    "pk.alias-token-for-test",
                )

    def test_service_specific_token_wins_and_wrapping_quotes_are_removed(self) -> None:
        environment = _token_environment(
            MAPBOX_ACCESS_TOKEN="pk.global-token",
            VECTOPLAN_APP_MAPBOX_ACCESS_TOKEN='"pk.service-token"',
        )
        with patch.dict(os.environ, environment):
            self.assertEqual(
                geocoding_client._configured_token(),
                "pk.service-token",
            )

    def test_search_uses_resolved_alias_without_leaking_it(self) -> None:
        response = Mock(status_code=200)
        response.json.return_value = {"features": []}
        environment = _token_environment(MAPBOX_TOKEN="pk.legacy-token")

        with (
            patch.dict(os.environ, environment),
            patch.object(
                geocoding_client.requests,
                "get",
                return_value=response,
            ) as get,
        ):
            result = geocoding_client.search_addresses("Berlin", limit=1)
            status = geocoding_client.geocoding_status()

        self.assertEqual(result, [])
        self.assertEqual(
            get.call_args.kwargs["params"]["access_token"],
            "pk.legacy-token",
        )
        self.assertTrue(status["configured"])
        self.assertNotIn("token", status)

    def test_missing_token_returns_default_berlin(self) -> None:
        environment = _token_environment(
            VECTOPLAN_APP_MAPBOX_GEOCODING_ENABLED="true",
        )

        with (
            patch.dict(os.environ, environment),
            patch.object(geocoding_client.requests, "get") as get,
        ):
            result = geocoding_client.search_addresses("Berlin")

        get.assert_not_called()
        self.assertEqual(len(result), 1)
        self.assert_default_berlin(result[0], reason="mapbox_token_missing")

    def test_disabled_mapbox_returns_default_berlin(self) -> None:
        environment = _token_environment(
            MAPBOX_ACCESS_TOKEN="pk.configured-token",
            VECTOPLAN_APP_MAPBOX_GEOCODING_ENABLED="false",
        )

        with (
            patch.dict(os.environ, environment),
            patch.object(geocoding_client.requests, "get") as get,
        ):
            result = geocoding_client.search_addresses("Berlin")

        get.assert_not_called()
        self.assert_default_berlin(result[0], reason="geocoding_disabled")

    def test_fallback_can_be_disabled(self) -> None:
        environment = _token_environment(
            VECTOPLAN_APP_GEOCODING_FALLBACK_ENABLED="false",
        )

        with (
            patch.dict(os.environ, environment),
            patch.object(geocoding_client.requests, "get") as get,
            self.assertRaises(geocoding_client.GeocodingError) as raised,
        ):
            geocoding_client.search_addresses("Berlin")

        get.assert_not_called()
        self.assertEqual(raised.exception.code, "mapbox_token_missing")

    def test_fallback_coordinates_and_label_are_configurable(self) -> None:
        environment = _token_environment(
            VECTOPLAN_APP_GEOCODING_FALLBACK_LONGITUDE="13.41",
            VECTOPLAN_APP_GEOCODING_FALLBACK_LATITUDE="52.51",
            VECTOPLAN_APP_GEOCODING_FALLBACK_LABEL="Berlin Testzentrum",
        )

        with patch.dict(os.environ, environment):
            result = geocoding_client.search_addresses("Berlin")[0]

        self.assertEqual(result["longitude"], 13.41)
        self.assertEqual(result["latitude"], 52.51)
        self.assertEqual(result["label"], "Berlin Testzentrum")
        self.assertEqual(result["address_text"], "Berlin Testzentrum")

    def test_rejected_token_returns_default_berlin(self) -> None:
        environment = _token_environment(
            MAPBOX_ACCESS_TOKEN="pk.rejected-token",
        )

        for status_code in (401, 403):
            with (
                self.subTest(status_code=status_code),
                patch.dict(os.environ, environment),
                patch.object(
                    geocoding_client.requests,
                    "get",
                    return_value=Mock(status_code=status_code),
                ),
            ):
                result = geocoding_client.search_addresses("Berlin")

            self.assert_default_berlin(result[0], reason="mapbox_token_rejected")

    def test_transient_mapbox_failures_return_default_berlin(self) -> None:
        environment = _token_environment(
            MAPBOX_ACCESS_TOKEN="pk.configured-token",
        )
        cases = (
            (geocoding_client.requests.Timeout("timeout"), "mapbox_timeout"),
            (
                geocoding_client.requests.ConnectionError("offline"),
                "mapbox_unavailable",
            ),
        )

        for side_effect, reason in cases:
            with (
                self.subTest(reason=reason),
                patch.dict(os.environ, environment),
                patch.object(
                    geocoding_client.requests,
                    "get",
                    side_effect=side_effect,
                ),
            ):
                result = geocoding_client.search_addresses("Berlin")

            self.assert_default_berlin(result[0], reason=reason)

    def test_rate_limit_and_upstream_errors_return_default_berlin(self) -> None:
        environment = _token_environment(
            MAPBOX_ACCESS_TOKEN="pk.configured-token",
        )

        for status_code, reason in (
            (429, "mapbox_rate_limited"),
            (500, "mapbox_request_failed"),
            (503, "mapbox_request_failed"),
        ):
            with (
                self.subTest(status_code=status_code),
                patch.dict(os.environ, environment),
                patch.object(
                    geocoding_client.requests,
                    "get",
                    return_value=Mock(status_code=status_code),
                ),
            ):
                result = geocoding_client.search_addresses("Berlin")

            self.assert_default_berlin(result[0], reason=reason)

    def test_invalid_mapbox_response_returns_default_berlin(self) -> None:
        environment = _token_environment(
            MAPBOX_ACCESS_TOKEN="pk.configured-token",
        )
        invalid_json = Mock(status_code=200)
        invalid_json.json.side_effect = ValueError("invalid json")
        invalid_shape = Mock(status_code=200)
        invalid_shape.json.return_value = {"features": "not-a-list"}

        for response in (invalid_json, invalid_shape):
            with (
                self.subTest(response=response),
                patch.dict(os.environ, environment),
                patch.object(
                    geocoding_client.requests,
                    "get",
                    return_value=response,
                ),
            ):
                result = geocoding_client.search_addresses("Berlin")

            self.assert_default_berlin(result[0], reason="mapbox_response_invalid")

    def test_fallback_id_resolves_without_mapbox_for_project_save(self) -> None:
        environment = _token_environment(MAPBOX_ACCESS_TOKEN="pk.valid-again")

        with (
            patch.dict(os.environ, environment),
            patch.object(geocoding_client.requests, "get") as get,
        ):
            response = geocoding_client.geocode_address(
                address="fallback:berlin-default",
                permanent=True,
            )

        get.assert_not_called()
        self.assertTrue(response["ok"])
        self.assertEqual(response["provider"], "fallback/default_berlin")
        self.assert_default_berlin(
            response["result"],
            reason="selected_fallback",
        )

    def test_address_resolve_uses_default_berlin_when_mapbox_is_unavailable(self) -> None:
        environment = _token_environment()

        with (
            patch.dict(os.environ, environment),
            patch.object(geocoding_client.requests, "get") as get,
        ):
            response = geocoding_client.geocode_address(
                address="Alexanderplatz 1, Berlin",
                permanent=True,
            )

        get.assert_not_called()
        self.assertTrue(response["ok"])
        self.assertEqual(response["provider"], "fallback/default_berlin")
        self.assert_default_berlin(
            response["result"],
            reason="mapbox_token_missing",
        )

    def test_invalid_and_short_queries_never_fall_back(self) -> None:
        environment = _token_environment()

        with (
            patch.dict(os.environ, environment),
            patch.object(geocoding_client.requests, "get") as get,
        ):
            self.assertEqual(geocoding_client.search_addresses("Be"), [])
            with self.assertRaises(geocoding_client.GeocodingError) as raised:
                geocoding_client.search_addresses("Berlin; DROP")

        get.assert_not_called()
        self.assertEqual(raised.exception.code, "geocoding_query_invalid")
        self.assertEqual(raised.exception.status_code, 422)

    def test_non_retryable_mapbox_client_error_remains_an_error(self) -> None:
        environment = _token_environment(
            MAPBOX_ACCESS_TOKEN="pk.configured-token",
        )

        with (
            patch.dict(os.environ, environment),
            patch.object(
                geocoding_client.requests,
                "get",
                return_value=Mock(status_code=400),
            ),
            self.assertRaises(geocoding_client.GeocodingError) as raised,
        ):
            geocoding_client.search_addresses("Berlin")

        self.assertEqual(raised.exception.code, "mapbox_request_failed")
        self.assertEqual(raised.exception.status_code, 422)

    def test_project_form_shows_safe_server_configuration_message(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "static"
            / "js"
            / "project"
            / "project_form.js"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'var errorMessage = trimString(error && error.message, "")',
            script,
        )
        self.assertIn(
            'setGeocoderStatus(errorMessage + " \\u00b7 Powered by Mapbox", "error")',
            script,
        )


if __name__ == "__main__":
    unittest.main()
