from routes.viewer_selection import _json_safe, _selection_payload


def test_project_parcel_multipolygon_coordinates_survive_state_sanitizing():
    selection = {
        "projectPublicId": "prj_world_edit_12345678",
        "coordinateSpace": "wgs84",
        "coveragePolicy": "cell-center",
        "parcels": [
            {
                "parcelId": "flurstuecke:42",
                "datasetId": "flurstuecke",
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[[[13.40491, 52.52081], [13.40501, 52.52081], [13.40491, 52.52081]]]],
                },
            }
        ],
    }

    safe = _json_safe({"last_map_selection": selection})
    normalized = _selection_payload(safe)

    coordinate = normalized["last_map_selection"]["parcels"][0]["geometry"]["coordinates"][0][0][0]
    assert coordinate == [13.40491, 52.52081]


def test_per_parcel_grid_state_survives_project_state_sanitizing():
    selection = {
        "projectPublicId": "prj_world_edit_12345678",
        "parcels": [],
        "parcelGridState": {
            "schemaVersion": "vectoplan-parcel-grid-state.v1",
            "mode": "boundary",
            "setbackMeters": 0,
            "influenceMeters": 3,
            "activeParcelId": "flurstuecke:42",
            "guides": [{
                "parcelId": "flurstuecke:42",
                "startLonLat": [13.40491, 52.52081],
                "endLonLat": [13.40501, 52.52081],
                "depthMeters": 4,
            }],
        },
    }

    normalized = _selection_payload(_json_safe({"last_map_selection": selection}))
    grid_state = normalized["last_map_selection"]["parcelGridState"]
    assert grid_state["activeParcelId"] == "flurstuecke:42"
    assert grid_state["guides"][0]["depthMeters"] == 4
