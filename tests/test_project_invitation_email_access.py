from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services import project_invitation_service as service


class _FakeInvitationModel:
    @classmethod
    def create_pending(cls, **kwargs):
        invitation = SimpleNamespace(
            id=31,
            public_id="inv_placeholder",
            project_id=kwargs["project_id"],
            project_public_id=kwargs["project_public_id"],
            email=kwargs["email"],
            email_normalized=kwargs["email"],
            role=kwargs["role"],
            status="pending",
        )
        return invitation, "plain-token"


class ProjectInvitationEmailAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = SimpleNamespace(id=11, public_id="prj_test", is_demo=False)
        self.actor = {
            "user_id": 2,
            "auth_user_id": "auth-owner",
            "authenticated": True,
            "persistent": True,
        }
        self.common_patchers = [
            patch.object(service, "ProjectInvitation", _FakeInvitationModel),
            patch.object(service, "db", SimpleNamespace(session=SimpleNamespace(flush=lambda: None))),
            patch.object(service, "resolve_project", return_value=self.project),
            patch.object(service, "get_actor_context", return_value=self.actor),
            patch.object(service, "_require_manage_permission", return_value=None),
            patch.object(service, "_write_audit_event"),
            patch.object(service, "_session_add"),
            patch.object(service, "_commit_or_flush"),
            patch.object(service, "_rollback_safely"),
            patch.object(service, "build_invitation_url", return_value="/invite/inv_placeholder"),
        ]
        for patcher in self.common_patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_registered_identity_receives_membership_immediately(self) -> None:
        identity = {
            "ok": True,
            "registered": True,
            "code": "registered_identity_loaded",
            "status_code": 200,
            "auth_user_id": "auth-target",
            "identity": {"display_name": "Ada Beispiel"},
        }
        linked_user = SimpleNamespace(id=7, auth_user_id="auth-target")
        membership = SimpleNamespace(id=19, project_id=11, user_id=7, role="editor", status="active")

        with (
            patch.object(service, "require_registered_email_identity", return_value=identity),
            patch.object(service, "ensure_linked_app_user", return_value=(linked_user, True)) as ensure_link,
            patch.object(service, "_find_membership", return_value=None),
            patch.object(service, "_find_active_invitation_for_email", return_value=None),
            patch.object(service, "_create_or_update_membership_from_invitation", return_value=(True, membership, "membership_created")),
            patch.object(service, "_sync_access_after_invitation_accept", return_value={"ok": True, "code": "ready"}),
        ):
            result = service.ProjectInvitationService().invite_by_email(
                self.project,
                email="ada@example.com",
                role="editor",
                actor_user_id=2,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.code, "project_member_access_granted")
        self.assertIs(result.membership, membership)
        self.assertTrue(result.data["direct_access"])
        ensure_link.assert_called_once()

    def test_unregistered_identity_creates_unsent_placeholder(self) -> None:
        identity = {
            "ok": False,
            "registered": False,
            "code": "user_not_registered",
            "status_code": 404,
            "email": "future@example.com",
        }

        with (
            patch.object(service, "require_registered_email_identity", return_value=identity),
            patch.object(service, "ensure_linked_app_user") as ensure_link,
            patch.object(service, "_find_active_invitation_for_email", return_value=None),
        ):
            result = service.ProjectInvitationService().invite_by_email(
                self.project,
                email="future@example.com",
                role="viewer",
                actor_user_id=2,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.code, "project_invitation_placeholder_created")
        self.assertFalse(result.data["direct_access"])
        self.assertTrue(result.data["email_dispatch_placeholder"])
        self.assertFalse(result.data["dispatch_sent"])
        ensure_link.assert_not_called()

    def test_role_grant_reactivates_a_removed_membership(self) -> None:
        membership = SimpleNamespace(
            role="viewer",
            status="revoked",
            can_view=False,
            can_edit=False,
            can_manage=False,
            can_delete=False,
            can_transfer=False,
            can_embed=False,
            revoked_at=object(),
            revoked_by_user_id=3,
            revoke_reason="removed",
            expires_at=object(),
            accepted_at=None,
        )

        service._apply_role_to_membership(membership, "editor")

        self.assertEqual(membership.role, "editor")
        self.assertEqual(membership.status, "active")
        self.assertTrue(membership.can_view)
        self.assertTrue(membership.can_edit)
        self.assertIsNone(membership.revoked_at)
        self.assertIsNone(membership.revoked_by_user_id)
        self.assertIsNone(membership.revoke_reason)
        self.assertIsNone(membership.expires_at)

if __name__ == "__main__":
    unittest.main()