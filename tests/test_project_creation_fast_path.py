from __future__ import annotations

from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, select_autoescape


APP_ROOT = Path(__file__).resolve().parents[1]


class ProjectCreationFastPathTests(unittest.TestCase):
    def test_dashboard_renders_when_optional_metrics_are_missing(self) -> None:
        environment = Environment(
            loader=FileSystemLoader(APP_ROOT / "templates"),
            autoescape=select_autoescape(("html",)),
        )

        html = environment.get_template(
            "viewer/partials/project_dashboard.html"
        ).render(
            _project_dashboard={
                "financial": {
                    "cost_ratio_percent": None,
                    "budget_display": "—",
                    "cost_display": "—",
                    "remaining_display": "—",
                },
                "readiness_percent": None,
                "schedule": {},
                "workload": {},
                "portfolio": {},
                "systems": [],
                "insights": [],
            },
            _map_path="",
            _editor3d_path="",
        )

        self.assertIn("Projekt-Cockpit", html)
        self.assertIn("--vp-meter-value: 0%", html)
        self.assertNotIn("None%", html)

    def test_api_create_defers_external_services_but_keeps_retryable_state(self) -> None:
        service = (APP_ROOT / "services" / "project_service.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("defer_external_provisioning=True", service)
        self.assertIn(
            "if should_provision and commit and not defer_external_provisioning:",
            service,
        )
        self.assertIn('code="chunk_provisioning_deferred"', service)
        self.assertIn('"code": "core_provisioning_deferred"', service)
        self.assertIn('"external_provisioning_deferred": bool(', service)

    def test_create_navigation_has_no_location_assign_reload(self) -> None:
        form = (
            APP_ROOT / "static" / "js" / "project" / "project_form.js"
        ).read_text(encoding="utf-8")
        shell = (APP_ROOT / "static" / "js" / "chat" / "main.js").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("window.parent.location.assign", form)
        self.assertNotIn("window.location.assign(url)", form)
        self.assertIn("function finishCreateNavigation(detail)", form)
        self.assertIn("win.history.replaceState(", form)
        self.assertIn('eventType === "vectoplan:project:created"', shell)
        self.assertIn('void setWorkspaceMode("project", {', shell)


if __name__ == "__main__":
    unittest.main()
