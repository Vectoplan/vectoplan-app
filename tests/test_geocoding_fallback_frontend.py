from __future__ import annotations

from pathlib import Path
import unittest


APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_FORM = (
    APP_ROOT / "static" / "js" / "project" / "project_form.js"
).read_text(encoding="utf-8")
ADDRESS_TEMPLATE = (
    APP_ROOT / "templates" / "viewer" / "partials" / "project_address.html"
).read_text(encoding="utf-8")
PROJECTS_API = (APP_ROOT / "routes" / "projects_api.py").read_text(encoding="utf-8")


class GeocodingFallbackFrontendContractTests(unittest.TestCase):
    def test_berlin_fallback_has_stable_identity_and_unambiguous_copy(self) -> None:
        self.assertIn(
            'var BERLIN_DEFAULT_FALLBACK_ID = "fallback:berlin-default";',
            PROJECT_FORM,
        )
        self.assertIn(
            '"Berlin (Standardstandort \\u2013 Mapbox nicht verf\\u00fcgbar)"',
            PROJECT_FORM,
        )
        self.assertIn('source === "default_berlin"', PROJECT_FORM)
        self.assertIn('provider === "default_berlin"', PROJECT_FORM)
        self.assertIn('provider === "fallback/default_berlin"', PROJECT_FORM)
        self.assertIn("suggestion.is_fallback", PROJECT_FORM)

    def test_fallback_selection_keeps_the_backend_contract(self) -> None:
        self.assertIn(
            "isBerlinFallback\n          ? BERLIN_DEFAULT_FALLBACK_ID",
            PROJECT_FORM,
        )
        self.assertIn(
            "address_mapbox_id: getValue(refs.addressMapboxId)",
            PROJECT_FORM,
        )
        self.assertIn(
            'option.setAttribute("data-geocoder-fallback", isBerlinFallback ? "berlin-default" : "false")',
            PROJECT_FORM,
        )

    def test_fallback_response_is_rendered_instead_of_becoming_an_error(self) -> None:
        self.assertIn(
            "(!response.ok || payload.ok === false) && !hasUsableBerlinFallback",
            PROJECT_FORM,
        )
        self.assertIn(
            "renderGeocoderSuggestions(responseItems, payload)",
            PROJECT_FORM,
        )

    def test_mapbox_attribution_is_preserved_only_on_the_normal_result_path(self) -> None:
        self.assertIn(
            ': "Adresse ausgew\\u00e4hlt \\u00b7 Powered by Mapbox"',
            PROJECT_FORM,
        )
        self.assertIn(
            ': "Adresse ausw\\u00e4hlen \\u00b7 Powered by Mapbox"',
            PROJECT_FORM,
        )
        self.assertIn(
            'containsBerlinFallback ? "warning" : "neutral"',
            PROJECT_FORM,
        )
        self.assertIn(
            "Wähle einen vorgeschlagenen Standort aus.",
            ADDRESS_TEMPLATE,
        )
        self.assertNotIn("Wähle eine Mapbox-Adresse aus.", ADDRESS_TEMPLATE)
        self.assertIn(
            '"attribution": None if fallback_used else "Mapbox"',
            PROJECTS_API,
        )
        self.assertIn(
            '"provider": "default_berlin" if fallback_used else "mapbox"',
            PROJECTS_API,
        )


if __name__ == "__main__":
    unittest.main()
