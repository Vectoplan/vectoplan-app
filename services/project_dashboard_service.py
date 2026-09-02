from __future__ import annotations

"""Build a small, browser-safe project cockpit from existing project data.

The service deliberately separates reported construction progress from system
readiness. Missing cost or schedule data stays missing; the dashboard never
invents estimates merely to fill a card.
"""

from datetime import date, datetime, timezone
import math
from typing import Any, Dict, Mapping, Optional


DASHBOARD_SETTING_FIELDS = frozenset(
    {
        "budget_total",
        "cost_actual",
        "planned_hours",
        "logged_hours",
        "completion_percent",
        "target_date",
        "gross_floor_area_m2",
    }
)

_DASHBOARD_FIELD_ALIASES = {
    "budgetTotal": "budget_total",
    "costActual": "cost_actual",
    "plannedHours": "planned_hours",
    "loggedHours": "logged_hours",
    "completionPercent": "completion_percent",
    "targetDate": "target_date",
    "grossFloorAreaM2": "gross_floor_area_m2",
}

_DASHBOARD_NUMBER_LIMITS = {
    "budget_total": (0.0, 1_000_000_000_000.0),
    "cost_actual": (0.0, 1_000_000_000_000.0),
    "planned_hours": (0.0, 100_000_000.0),
    "logged_hours": (0.0, 100_000_000.0),
    "completion_percent": (0.0, 100.0),
    "gross_floor_area_m2": (0.0, 1_000_000_000.0),
}


class ProjectDashboardValidationError(ValueError):
    """A stable 422 error for malformed project-cockpit settings."""

    def __init__(self, code: str, message: str, *, field: str = "settings.dashboard") -> None:
        super().__init__(message)
        self.code = str(code or "project_dashboard_invalid")[:120]
        self.message = str(message or "Die Steuerungsdaten sind ungültig.")[:1000]
        self.field = str(field or "settings.dashboard")[:160]
        self.status_code = 422


def _normalized_number(value: Any, *, field: str, minimum: float, maximum: float) -> Optional[float | int]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ProjectDashboardValidationError(
            "project_dashboard_number_invalid",
            f"{field} muss eine endliche Zahl sein.",
            field=f"settings.dashboard.{field}",
        )
    try:
        parsed = float(str(value).strip().replace(" ", "").replace(",", "."))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProjectDashboardValidationError(
            "project_dashboard_number_invalid",
            f"{field} muss eine endliche Zahl sein.",
            field=f"settings.dashboard.{field}",
        ) from exc
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        raise ProjectDashboardValidationError(
            "project_dashboard_number_out_of_range",
            f"{field} muss zwischen {_format_number(minimum)} und {_format_number(maximum)} liegen.",
            field=f"settings.dashboard.{field}",
        )
    return int(parsed) if parsed.is_integer() else parsed


def normalize_project_dashboard_patch(value: Any) -> Dict[str, Any]:
    """Whitelist and normalize the seven browser-managed cockpit fields."""
    if not isinstance(value, Mapping):
        raise ProjectDashboardValidationError(
            "project_dashboard_mapping_required",
            "settings.dashboard muss ein Objekt sein.",
        )

    normalized: Dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _DASHBOARD_FIELD_ALIASES.get(str(raw_key), str(raw_key))
        if key not in DASHBOARD_SETTING_FIELDS:
            raise ProjectDashboardValidationError(
                "project_dashboard_field_not_allowed",
                f"Das Steuerungsfeld {raw_key!s} ist nicht erlaubt.",
                field=f"settings.dashboard.{raw_key!s}",
            )
        if key in normalized:
            raise ProjectDashboardValidationError(
                "project_dashboard_field_duplicate",
                f"Das Steuerungsfeld {key} wurde mehrfach übermittelt.",
                field=f"settings.dashboard.{key}",
            )
        if key == "target_date":
            if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
                normalized[key] = None
                continue
            text = str(raw_value).strip()
            try:
                parsed_date = date.fromisoformat(text)
            except (TypeError, ValueError) as exc:
                raise ProjectDashboardValidationError(
                    "project_dashboard_date_invalid",
                    "target_date muss ein gültiges ISO-Datum (JJJJ-MM-TT) sein.",
                    field="settings.dashboard.target_date",
                ) from exc
            if parsed_date.isoformat() != text:
                raise ProjectDashboardValidationError(
                    "project_dashboard_date_invalid",
                    "target_date muss ein gültiges ISO-Datum (JJJJ-MM-TT) sein.",
                    field="settings.dashboard.target_date",
                )
            normalized[key] = parsed_date.isoformat()
            continue

        minimum, maximum = _DASHBOARD_NUMBER_LIMITS[key]
        normalized[key] = _normalized_number(
            raw_value,
            field=key,
            minimum=minimum,
            maximum=maximum,
        )
    return normalized


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return default


def _number(value: Any, *, minimum: float = 0.0, maximum: float = 1_000_000_000_000.0) -> Optional[float]:
    try:
        if value is None or isinstance(value, bool) or str(value).strip() == "":
            return None
        parsed = float(str(value).replace(" ", "").replace(",", "."))
        if not math.isfinite(parsed):
            return None
        if parsed < minimum or parsed > maximum:
            return None
        return parsed
    except (TypeError, ValueError, OverflowError):
        return None


def _integer(value: Any, *, minimum: int = 0, maximum: int = 100) -> Optional[int]:
    number = _number(value, minimum=float(minimum), maximum=float(maximum))
    return int(round(number)) if number is not None else None


def _boolean(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on", "ready", "ok", "synced", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "failed", "error", "disabled", ""}:
        return False
    return default


def _date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _format_number(value: Optional[float], *, decimals: int = 0) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")


def _format_money(value: Optional[float], currency: str) -> str:
    return "—" if value is None else f"{_format_number(value)} {currency}"


def _published_count(publication: Mapping[str, Any]) -> int:
    values = _mapping(
        publication.get("effective_published_workspaces")
        or publication.get("effectivePublishedWorkspaces")
        or publication.get("published_workspaces")
        or publication.get("publishedWorkspaces")
    )
    return sum(1 for value in values.values() if _boolean(value))


def _team_count(project: Mapping[str, Any]) -> Optional[int]:
    members = project.get("members")
    if isinstance(members, (list, tuple)):
        return len(members)
    settings = _mapping(project.get("settings"))
    dashboard = _mapping(settings.get("dashboard") or settings.get("project_dashboard"))
    return _integer(_first(dashboard, "team_size", "teamSize"), minimum=0, maximum=100_000)


def build_project_dashboard_view(
    project: Mapping[str, Any] | None,
    *,
    chunk: Mapping[str, Any] | None = None,
    publication: Mapping[str, Any] | None = None,
    today: date | None = None,
) -> Dict[str, Any]:
    project_view = _mapping(project)
    chunk_view = _mapping(chunk or project_view.get("chunk"))
    publication_view = _mapping(publication or project_view.get("publication"))
    settings = _mapping(project_view.get("settings"))
    dashboard = _mapping(
        settings.get("dashboard")
        or settings.get("project_dashboard")
        or settings.get("projectDashboard")
        or settings.get("metrics")
    )

    currency = str(_first(dashboard, "currency", default="EUR")).strip().upper()[:3] or "EUR"
    currency_symbol = "€" if currency == "EUR" else currency
    budget_total = _number(_first(dashboard, "budget_total", "budgetTotal", "budget"))
    cost_actual = _number(_first(dashboard, "cost_actual", "costActual", "actual_cost", "actualCost"))
    budget_remaining = budget_total - cost_actual if budget_total is not None and cost_actual is not None else None
    cost_ratio = (
        max(0.0, cost_actual / budget_total * 100.0)
        if budget_total not in (None, 0) and cost_actual is not None
        else None
    )

    planned_hours = _number(_first(dashboard, "planned_hours", "plannedHours"), maximum=100_000_000)
    logged_hours = _number(_first(dashboard, "logged_hours", "loggedHours"), maximum=100_000_000)
    hours_remaining = planned_hours - logged_hours if planned_hours is not None and logged_hours is not None else None
    delivery_progress = _integer(
        _first(dashboard, "completion_percent", "completionPercent", "progress_percent", "progressPercent"),
        minimum=0,
        maximum=100,
    )

    today_value = today or datetime.now(timezone.utc).date()
    start_date = _date(_first(dashboard, "start_date", "startDate", default=project_view.get("created_at") or project_view.get("createdAt")))
    target_date = _date(_first(dashboard, "target_date", "targetDate", "deadline"))
    remaining_days = (target_date - today_value).days if target_date else None
    elapsed_days = max(0, (today_value - start_date).days) if start_date else None
    gross_floor_area_m2 = _number(
        _first(dashboard, "gross_floor_area_m2", "grossFloorAreaM2", "gfa_m2", "gfaM2"),
        maximum=1_000_000_000,
    )

    address_text = str(project_view.get("address_text") or project_view.get("addressText") or "").strip()
    cost_center = str(project_view.get("cost_center") or project_view.get("costCenter") or settings.get("cost_center") or "").strip()
    chunk_ready = _boolean(chunk_view.get("ready") or project_view.get("chunk_ready"))
    access_sync_required = _boolean(_first(chunk_view, "access_sync_required", "accessSyncRequired", default=False))
    access_status = str(
        _first(
            chunk_view,
            "access_sync_status",
            "accessSyncStatus",
            default="not_required" if not access_sync_required else "pending",
        )
    ).lower()
    access_sync_ready_raw = _first(chunk_view, "access_sync_ready", "accessSyncReady", default=None)
    if not access_sync_required:
        access_sync_ready = False
    elif access_sync_ready_raw is None:
        access_sync_ready = access_status in {"ready", "synced", "ok"}
    else:
        access_sync_ready = _boolean(access_sync_ready_raw, False)
    if access_status in {"failed", "error", "repair_required"}:
        access_sync_ready = False
    access_sync_satisfied = bool(not access_sync_required or access_sync_ready)
    configured = _boolean(project_view.get("is_configured") or project_view.get("isConfigured"))
    published_count = _published_count(publication_view)

    readiness_checks = [
        (15, bool(str(project_view.get("name") or "").strip()) and bool(cost_center)),
        (15, bool(address_text)),
        (25, chunk_ready),
        (15, access_sync_satisfied),
        (15, configured),
        (15, published_count > 0 or str(project_view.get("visibility") or "private") != "private"),
    ]
    readiness_percent = sum(weight for weight, ready in readiness_checks if ready)

    provisioning_status = str(chunk_view.get("provisioning_status") or chunk_view.get("provisioningStatus") or "pending").lower()
    systems = [
        {
            "key": "location",
            "label": "Standort",
            "status": "ready" if address_text else "attention",
            "detail": address_text or "Adresse und Georeferenz fehlen",
        },
        {
            "key": "world",
            "label": "3D-Welt",
            "status": "ready" if chunk_ready else ("error" if provisioning_status in {"failed", "error", "repair_required"} else "pending"),
            "detail": "Chunk-Welt bereit" if chunk_ready else f"Bereitstellung: {provisioning_status}",
        },
        {
            "key": "team",
            "label": "Team-Sync",
            "status": (
                "neutral"
                if not access_sync_required
                else "ready"
                if access_sync_ready
                else "error"
                if access_status in {"failed", "error", "repair_required"}
                else "pending"
            ),
            "detail": (
                "Nicht erforderlich"
                if not access_sync_required
                else "Zugriffe synchron"
                if access_sync_ready
                else f"Status: {access_status}"
            ),
        },
        {
            "key": "publication",
            "label": "Freigaben",
            "status": "ready" if published_count else "neutral",
            "detail": f"{published_count} Arbeitsbereiche veröffentlicht" if published_count else "Noch keine Arbeitsbereiche veröffentlicht",
        },
    ]

    insights = []
    if budget_total is None:
        insights.append({"tone": "info", "title": "Budget ergänzen", "text": "Mit einem Projektbudget werden Kostenstand und Restbudget sichtbar."})
    elif cost_actual is not None and cost_actual > budget_total:
        insights.append({"tone": "danger", "title": "Budget überschritten", "text": f"Die erfassten Kosten liegen {_format_money(cost_actual - budget_total, currency_symbol)} über dem Budget."})
    if target_date is None:
        insights.append({"tone": "info", "title": "Zieltermin festlegen", "text": "Ein Zieltermin macht verbleibende Zeit und Terminrisiken sichtbar."})
    elif remaining_days is not None and remaining_days < 0:
        insights.append({"tone": "danger", "title": "Zieltermin überschritten", "text": f"Der Zieltermin ist seit {abs(remaining_days)} Tagen überschritten."})
    if not chunk_ready:
        insights.append({"tone": "warning", "title": "3D-Welt noch nicht bereit", "text": "Die Chunk-Bereitstellung muss abgeschlossen sein, bevor der Editor vollständig nutzbar ist."})
    if not address_text:
        insights.append({"tone": "warning", "title": "Standort fehlt", "text": "Adresse und Koordinate werden für Karte, Gelände und Berliner Geodaten benötigt."})
    if not cost_center:
        insights.append({"tone": "warning", "title": "Kostenstelle fehlt", "text": "Eine Kostenstelle verbindet Projekt, Belege und Freigaben eindeutig."})
    if not configured:
        insights.append({"tone": "info", "title": "Grundkonfiguration abschließen", "text": "Die Projektkonfiguration ist noch nicht als vollständig markiert."})
    if access_sync_required and not access_sync_ready:
        insights.append({"tone": "danger" if access_status in {"failed", "error", "repair_required"} else "warning", "title": "Team-Sync prüfen", "text": f"Der Zugriffssync ist noch nicht bereit (Status: {access_status})."})
    if published_count == 0 and str(project_view.get("visibility") or "private") == "private":
        insights.append({"tone": "info", "title": "Freigabe offen", "text": "Das Projekt ist privat und noch für keinen Arbeitsbereich veröffentlicht."})
    if not insights:
        if readiness_percent == 100:
            insights.append({"tone": "success", "title": "Projekt technisch bereit", "text": "Die zentralen Projekt- und Laufzeitprüfungen sind ohne offene Warnung."})
        else:
            insights.append({"tone": "warning", "title": "Technische Einrichtung prüfen", "text": f"Die technische Bereitschaft liegt bei {readiness_percent} Prozent."})

    insight_priority = {"danger": 0, "warning": 1, "info": 2, "success": 3}
    insights.sort(key=lambda item: insight_priority.get(str(item.get("tone") or "info"), 2))

    return {
        "schema_version": "vectoplan-project-dashboard.v1",
        "configured": bool(budget_total is not None or target_date is not None or delivery_progress is not None),
        "readiness_percent": readiness_percent,
        "delivery_progress_percent": delivery_progress,
        "financial": {
            "configured": budget_total is not None,
            "currency": currency,
            "currency_symbol": currency_symbol,
            "budget_total": budget_total,
            "cost_actual": cost_actual,
            "budget_remaining": budget_remaining,
            "cost_ratio_percent": round(cost_ratio, 1) if cost_ratio is not None else None,
            "budget_display": _format_money(budget_total, currency_symbol),
            "cost_display": _format_money(cost_actual, currency_symbol),
            "remaining_display": _format_money(budget_remaining, currency_symbol),
            "status": "over" if budget_remaining is not None and budget_remaining < 0 else ("ready" if budget_total is not None else "missing"),
        },
        "schedule": {
            "configured": target_date is not None,
            "start_date": start_date.isoformat() if start_date else None,
            "target_date": target_date.isoformat() if target_date else None,
            "remaining_days": remaining_days,
            "elapsed_days": elapsed_days,
            "target_display": target_date.strftime("%d.%m.%Y") if target_date else "—",
            "remaining_display": (
                f"{remaining_days} Tage verbleibend" if remaining_days is not None and remaining_days >= 0
                else (f"{abs(remaining_days)} Tage überfällig" if remaining_days is not None else "Noch nicht geplant")
            ),
            "status": "overdue" if remaining_days is not None and remaining_days < 0 else ("ready" if target_date else "missing"),
        },
        "workload": {
            "planned_hours": planned_hours,
            "logged_hours": logged_hours,
            "remaining_hours": hours_remaining,
            "planned_display": _format_number(planned_hours),
            "logged_display": _format_number(logged_hours),
        },
        "portfolio": {
            "team_count": _team_count(project_view),
            "published_workspace_count": published_count,
            "version_count": len(project_view.get("versions") or []) if isinstance(project_view.get("versions"), (list, tuple)) else 0,
            "cost_center": cost_center or None,
            "gross_floor_area_m2": gross_floor_area_m2,
            "gross_floor_area_display": _format_number(gross_floor_area_m2),
        },
        "systems": systems,
        "insights": insights[:4],
        "settings": {
            "budget_total": budget_total,
            "cost_actual": cost_actual,
            "planned_hours": planned_hours,
            "logged_hours": logged_hours,
            "completion_percent": delivery_progress,
            "target_date": target_date.isoformat() if target_date else "",
            "gross_floor_area_m2": gross_floor_area_m2,
        },
    }


__all__ = [
    "DASHBOARD_SETTING_FIELDS",
    "ProjectDashboardValidationError",
    "build_project_dashboard_view",
    "normalize_project_dashboard_patch",
]
