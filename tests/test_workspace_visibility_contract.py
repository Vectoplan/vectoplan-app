from pathlib import Path


MAIN_JS = (
    Path(__file__).parents[1] / "static" / "js" / "chat" / "main.js"
).read_text(encoding="utf-8")


def test_hidden_editor_workspace_receives_a_pause_signal() -> None:
    assert "function postWorkspaceFrameVisibility(frame, active)" in MAIN_JS
    assert 'type: "vectoplan-app:workspace-visibility"' in MAIN_JS
    assert "const visible = Boolean(active) && uiState.desktopVisible !== false" in MAIN_JS
    assert "visible," in MAIN_JS
    assert "postWorkspaceFrameVisibility(frame, active);" in MAIN_JS


def test_loaded_editor_receives_current_visibility_state() -> None:
    assert 'postWorkspaceFrameVisibility(frame, frame.dataset.workspaceActive === "true")' in MAIN_JS


def test_desktop_client_visibility_is_forwarded_to_nested_workspaces() -> None:
    assert "function wireDesktopVisibilityBridge()" in MAIN_JS
    assert 'event.data.type !== "vectoplan-client:service-visibility"' in MAIN_JS
    assert "Boolean(active) && uiState.desktopVisible !== false" in MAIN_JS
