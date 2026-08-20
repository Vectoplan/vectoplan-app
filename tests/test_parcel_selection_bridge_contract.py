from pathlib import Path


ROOT = Path(__file__).parents[1]
MAIN_JS = (ROOT / "static" / "js" / "chat" / "main.js").read_text(encoding="utf-8")


def test_new_parcel_centres_coordinate_until_user_moves_marker() -> None:
    assert "const parcelCenterCoordinate" in MAIN_JS
    assert "const automaticCoordinateForSelection" in MAIN_JS
    assert "previous?.projectCoordinateManualOverride" in MAIN_JS
    assert "if (automaticCoordinate) void persistProjectCoordinate(automaticCoordinate)" in MAIN_JS


def test_map_drag_persists_manual_override_and_project_coordinate() -> None:
    assert "projectCoordinateManualOverride: detail.projectCoordinateManualOverride == null" in MAIN_JS
    assert "void persistSelection()" in MAIN_JS
    assert "void persistProjectCoordinate(currentSelection.projectCoordinate)" in MAIN_JS
    assert 'pathValue("projectApiPath") || cfgValue("projectApiPath")' in MAIN_JS


def test_manual_coordinate_survives_stale_parcel_and_catalog_messages() -> None:
    assert "previousSelection.projectCoordinateManualOverride === true" in MAIN_JS
    assert "? previousSelection.projectCoordinate" in MAIN_JS
    assert "currentSelection.projectCoordinateManualOverride !== true" in MAIN_JS


def test_editor_parcel_change_gets_monotonic_revision_before_map_sync() -> None:
    assert '(type === "vectoplan-editor:parcel-selection-changed" && fromEditor)' in MAIN_JS
    assert "Number(previousSelection.revision || 0) + 1" in MAIN_JS
    assert "broadcastSelection()" in MAIN_JS


def test_persistence_is_ordered_and_late_hydration_cannot_overwrite_edits() -> None:
    assert "selectionPersistQueue = selectionPersistQueue" in MAIN_JS
    assert "coordinatePersistQueue = coordinatePersistQueue" in MAIN_JS
    assert "const selectionSnapshot = JSON.parse(JSON.stringify(currentSelection))" in MAIN_JS
    assert "const hydrationMutationSerial = localMutationSerial" in MAIN_JS
    assert "if (localMutationSerial === hydrationMutationSerial)" in MAIN_JS


def test_3d_keeps_hidden_map_frame_available_for_parcel_catalogue() -> None:
    assert "function prepareParcelCatalogFrame()" in MAIN_JS
    assert "if (editorActive) prepareParcelCatalogFrame()" in MAIN_JS
    assert 'parcelCatalogSource: "true"' in MAIN_JS


def test_selected_geometry_is_retained_for_immediate_reselection() -> None:
    assert "...currentSelection.parcels" in MAIN_JS
    assert "...availableParcels" in MAIN_JS
