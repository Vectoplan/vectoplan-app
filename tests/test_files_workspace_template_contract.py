from pathlib import Path


TEMPLATE = (Path(__file__).parents[1] / "templates" / "chat_viewer.html").read_text(encoding="utf-8")


def test_files_workspace_uses_project_membership_not_legacy_cloud_entitlement() -> None:
    assert "files_workspace_enabled = true if member_service_workspace_enabled else false" in TEMPLATE
    assert "member_service_workspace_enabled and has_cloud_access" not in TEMPLATE
    assert 'data-workspace-mode-button="files"' in TEMPLATE
    assert "{% if not files_workspace_enabled %}disabled aria-disabled=\"true\"{% endif %}" in TEMPLATE
