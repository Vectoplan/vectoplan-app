from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services import project_access_context as access_context


class ProjectAccessContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = SimpleNamespace(
            id=42,
            public_id="prj_public",
            is_demo=False,
            visibility="public",
            is_public=True,
        )
        self.current_user = {
            "user_id": 7,
            "auth_user_id": "auth-user-7",
            "authenticated": True,
            "persistent": True,
            "demo_mode": False,
        }
        self.public_access = access_context.PublicAccessEvaluation(
            allowed=True,
            status_code=200,
            code="public_project_allowed",
            visibility="public",
            publication_enabled=True,
            workspace_published=True,
            require_auth=False,
            require_project_permission=False,
            workspace="project",
            reason="published_workspace",
        )

    def test_authenticated_owner_permissions_win_over_public_access(self) -> None:
        owner_permission = access_context._MemberPermissionEvaluation(
            allowed=True,
            status_code=200,
            code="project_permission_allowed",
            role="owner",
            permissions=access_context._permissions_owner(),
            reason="owner_user_id_match",
        )

        with (
            patch.object(
                access_context,
                "evaluate_public_project_access",
                return_value=self.public_access,
            ),
            patch.object(
                access_context,
                "_evaluate_member_permission",
                return_value=owner_permission,
            ) as evaluate_member,
        ):
            result = access_context._resolve_project_access_uncached(
                self.project,
                current_user_context=self.current_user,
                workspace="project",
                action="view",
            )

        evaluate_member.assert_called_once_with(
            self.project,
            user_id=7,
            workspace="project",
            action="view",
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.access_mode, "authenticated")
        self.assertEqual(result.role, "owner")
        self.assertFalse(result.public_viewer)
        self.assertFalse(result.read_only)
        self.assertTrue(result.permissions["manage"])
        self.assertTrue(result.permissions["view_settings"])

    def test_public_access_remains_fallback_for_authenticated_non_member(self) -> None:
        no_member_permission = access_context._MemberPermissionEvaluation(
            allowed=False,
            status_code=403,
            code="project_permission_denied",
            reason="no_membership_or_public_access",
        )

        with (
            patch.object(
                access_context,
                "evaluate_public_project_access",
                return_value=self.public_access,
            ),
            patch.object(
                access_context,
                "_evaluate_member_permission",
                return_value=no_member_permission,
            ),
        ):
            result = access_context._resolve_project_access_uncached(
                self.project,
                current_user_context=self.current_user,
                workspace="project",
                action="view",
            )

        self.assertTrue(result.allowed)
        self.assertEqual(result.access_mode, "public")
        self.assertEqual(result.role, "public_viewer")
        self.assertTrue(result.public_viewer)
        self.assertTrue(result.read_only)

    def test_anonymous_public_access_does_not_query_memberships(self) -> None:
        anonymous_user = {
            "authenticated": False,
            "persistent": False,
            "demo_mode": False,
        }

        with (
            patch.object(
                access_context,
                "evaluate_public_project_access",
                return_value=self.public_access,
            ),
            patch.object(access_context, "_evaluate_member_permission") as evaluate_member,
        ):
            result = access_context._resolve_project_access_uncached(
                self.project,
                current_user_context=anonymous_user,
                workspace="project",
                action="view",
            )

        evaluate_member.assert_not_called()
        self.assertTrue(result.allowed)
        self.assertEqual(result.access_mode, "public")
        self.assertTrue(result.public_viewer)


if __name__ == "__main__":
    unittest.main()
