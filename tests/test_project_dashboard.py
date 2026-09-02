from __future__ import annotations

from datetime import date
from pathlib import Path
import unittest

from services.project_dashboard_service import (
    ProjectDashboardValidationError,
    build_project_dashboard_view,
    normalize_project_dashboard_patch,
)


class ProjectDashboardServiceTests(unittest.TestCase):
    def test_builds_real_financial_schedule_and_readiness_metrics(self) -> None:
        dashboard = build_project_dashboard_view(
            {
                "name": "Berlin Mitte",
                "address_text": "Alexanderplatz 1, 10178 Berlin",
                "visibility": "link",
                "is_configured": True,
                "members": [{"id": 1}, {"id": 2}],
                "settings": {
                    "cost_center": "KST-BERLIN",
                    "dashboard": {
                        "budget_total": 1_000_000,
                        "cost_actual": 250_000,
                        "planned_hours": 4_000,
                        "logged_hours": 1_100,
                        "completion_percent": 35,
                        "target_date": "2027-03-31",
                        "gross_floor_area_m2": 12_500,
                    },
                },
            },
            chunk={
                "ready": True,
                "access_sync_required": True,
                "access_sync_ready": True,
                "provisioning_status": "ready",
                "access_sync_status": "ready",
            },
            publication={"effective_published_workspaces": {"editor3d": True, "map": True}},
            today=date(2026, 9, 1),
        )

        self.assertEqual(dashboard["schema_version"], "vectoplan-project-dashboard.v1")
        self.assertEqual(dashboard["readiness_percent"], 100)
        self.assertEqual(dashboard["delivery_progress_percent"], 35)
        self.assertEqual(dashboard["financial"]["budget_remaining"], 750_000)
        self.assertEqual(dashboard["financial"]["cost_ratio_percent"], 25.0)
        self.assertEqual(dashboard["workload"]["remaining_hours"], 2_900)
        self.assertEqual(dashboard["schedule"]["remaining_days"], 211)
        self.assertEqual(dashboard["portfolio"]["team_count"], 2)
        self.assertEqual(dashboard["portfolio"]["published_workspace_count"], 2)
        self.assertEqual(dashboard["portfolio"]["gross_floor_area_m2"], 12_500)

    def test_missing_business_data_stays_missing_instead_of_being_estimated(self) -> None:
        dashboard = build_project_dashboard_view(
            {"name": "Ungeplantes Projekt", "settings": {"cost_center": "KST-1"}},
            chunk={"ready": False},
            today=date(2026, 9, 1),
        )

        self.assertFalse(dashboard["configured"])
        self.assertIsNone(dashboard["delivery_progress_percent"])
        self.assertIsNone(dashboard["financial"]["budget_total"])
        self.assertIsNone(dashboard["schedule"]["target_date"])
        self.assertEqual(dashboard["financial"]["budget_display"], "—")
        self.assertTrue(any(item["title"] == "Budget ergänzen" for item in dashboard["insights"]))

    def test_failed_access_sync_never_reports_technical_success(self) -> None:
        dashboard = build_project_dashboard_view(
            {
                "name": "Nicht bereit",
                "address_text": "Berlin",
                "visibility": "public",
                "is_configured": True,
                "settings": {
                    "cost_center": "KST-1",
                    "dashboard": {"budget_total": 10, "target_date": "2027-01-01"},
                },
            },
            chunk={
                "ready": True,
                "access_sync_required": True,
                "access_sync_ready": False,
                "access_sync_status": "failed",
            },
            publication={"effective_published_workspaces": {"editor3d": True}},
            today=date(2026, 9, 1),
        )

        self.assertLess(dashboard["readiness_percent"], 100)
        self.assertFalse(any(item["title"] == "Projekt technisch bereit" for item in dashboard["insights"]))
        self.assertTrue(any(item["title"] == "Team-Sync prüfen" for item in dashboard["insights"]))
        self.assertEqual(dashboard["insights"][0]["tone"], "danger")

    def test_access_sync_not_required_is_neutral_and_does_not_lower_readiness(self) -> None:
        dashboard = build_project_dashboard_view(
            {
                "name": "Solo-Projekt",
                "address_text": "Berlin",
                "visibility": "public",
                "is_configured": True,
                "settings": {
                    "cost_center": "KST-1",
                    "dashboard": {"budget_total": 10, "target_date": "2027-01-01"},
                },
            },
            chunk={
                "ready": True,
                "access_sync_required": False,
                "access_sync_ready": False,
                "access_sync_status": "not_required",
            },
            publication={"effective_published_workspaces": {"editor3d": True}},
            today=date(2026, 9, 1),
        )

        team_system = next(item for item in dashboard["systems"] if item["key"] == "team")
        self.assertEqual(dashboard["readiness_percent"], 100)
        self.assertEqual(team_system["status"], "neutral")
        self.assertEqual(team_system["detail"], "Nicht erforderlich")

    def test_non_finite_numbers_are_treated_as_missing(self) -> None:
        for invalid in ("NaN", "Infinity", "-Infinity", True, ""):
            with self.subTest(invalid=invalid):
                dashboard = build_project_dashboard_view(
                    {"settings": {"dashboard": {"budget_total": invalid, "completion_percent": invalid}}},
                    today=date(2026, 9, 1),
                )
                self.assertIsNone(dashboard["financial"]["budget_total"])
                self.assertIsNone(dashboard["delivery_progress_percent"])

    def test_dashboard_patch_normalizes_only_the_seven_supported_fields(self) -> None:
        normalized = normalize_project_dashboard_patch(
            {
                "budgetTotal": "2500,50",
                "cost_actual": "",
                "planned_hours": 100,
                "loggedHours": "25.5",
                "completion_percent": "35",
                "targetDate": "2027-03-31",
                "gross_floor_area_m2": "12500",
            }
        )

        self.assertEqual(normalized["budget_total"], 2500.5)
        self.assertIsNone(normalized["cost_actual"])
        self.assertEqual(normalized["logged_hours"], 25.5)
        self.assertEqual(normalized["target_date"], "2027-03-31")
        self.assertEqual(len(normalized), 7)

    def test_dashboard_patch_rejects_unknown_invalid_and_out_of_range_values(self) -> None:
        cases = (
            ({"currency": "EUR"}, "project_dashboard_field_not_allowed"),
            ({"budget_total": "NaN"}, "project_dashboard_number_out_of_range"),
            ({"completion_percent": 101}, "project_dashboard_number_out_of_range"),
            ({"target_date": "2027-02-29"}, "project_dashboard_date_invalid"),
        )
        for payload, expected_code in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ProjectDashboardValidationError) as raised:
                    normalize_project_dashboard_patch(payload)
                self.assertEqual(raised.exception.status_code, 422)
                self.assertEqual(raised.exception.code, expected_code)


class ProjectDashboardContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]

    def test_owner_cockpit_is_conditional_and_settings_are_collapsed(self) -> None:
        template = (self.root / "templates" / "viewer" / "project.html").read_text(encoding="utf-8")
        cockpit = (
            self.root / "templates" / "viewer" / "partials" / "project_dashboard.html"
        ).read_text(encoding="utf-8")

        marker = '{% if _show_project_dashboard %}\n        {% include "viewer/partials/project_dashboard.html" %}'
        self.assertIn(marker, template)
        self.assertIn("data-project-settings-disclosure", template)
        self.assertIn(
            "'dashboard': {'settings': _project_dashboard.get('settings', {})} if _dashboard_can_manage else {}",
            template,
        )
        self.assertIn("Projekt-Cockpit", cockpit)
        self.assertIn("ohne geschätzte Fantasiewerte", cockpit)

    def test_dashboard_fields_are_saved_inside_project_settings(self) -> None:
        script = (
            self.root / "static" / "js" / "project" / "project_form.js"
        ).read_text(encoding="utf-8")

        self.assertIn('budgetTotal: queryById("projectBudgetTotal")', script)
        self.assertIn("if (state.dashboardDirty && canManageProjectSettings())", script)
        self.assertIn("if (state.costCenterDirty && canManageProjectSettings()", script)
        self.assertIn("refs.completionPercent", script)
        self.assertIn("refs.grossFloorAreaM2", script)
        self.assertIn("state.dashboardDirty = true", script)
        self.assertIn("function validateOptionalDashboardNumber", script)
        self.assertIn("await refreshDashboardFromServer()", script)
        self.assertIn("function syncDashboardPreview()", script)
        self.assertIn("data-dashboard-budget-total", script)

    def test_dashboard_assets_use_the_same_new_cache_revision(self) -> None:
        template = (self.root / "templates" / "viewer" / "project.html").read_text(encoding="utf-8")
        self.assertEqual(template.count("project-cockpit-20260901-2"), 2)

    def test_server_deep_merges_and_protects_dashboard_settings(self) -> None:
        service = (self.root / "services" / "project_service.py").read_text(encoding="utf-8")

        self.assertIn("def _merge_project_settings(existing: Any, incoming: Any)", service)
        self.assertIn('require_project_permission(project, "manage_settings", uid', service)
        self.assertIn("normalize_project_dashboard_patch(normalized.pop(dashboard_key))", service)
        self.assertIn("private_settings_allowed = bool(serialization_permissions.can_view_settings)", service)
        self.assertNotIn("include_private_settings", service)
        self.assertIn("except ProjectDashboardValidationError as exc:", service)
        self.assertIn('code="project_settings_permission_denied"', service)
        self.assertIn('payload.pop(private_key, None)', service)

    def test_explicit_settings_permissions_flow_through_context_and_template(self) -> None:
        context = (self.root / "services" / "project_workspace_context.py").read_text(encoding="utf-8")
        template = (self.root / "templates" / "viewer" / "project.html").read_text(encoding="utf-8")

        self.assertIn("show_project_dashboard = bool(\n            can_view_settings", context)
        self.assertIn("can_edit_project_dashboard = bool(\n            show_project_dashboard\n            and can_manage_settings", context)
        self.assertIn("data-project-can-view-settings", template)
        self.assertIn("data-project-can-manage-settings", template)
        self.assertIn("{% if _dashboard_can_manage %}", template)

    def test_fallback_context_always_defines_dashboard(self) -> None:
        route = (self.root / "routes" / "viewer.py").read_text(encoding="utf-8")
        template = (self.root / "templates" / "viewer" / "project.html").read_text(encoding="utf-8")

        self.assertIn('"project_dashboard": {},', route)
        self.assertIn("project_dashboard|default(_project.get('dashboard', {}), true)", template)


if __name__ == "__main__":
    unittest.main()
