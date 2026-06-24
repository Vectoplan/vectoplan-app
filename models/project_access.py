# services/vectoplan-app/models/project_access.py
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .base import (
    SerializationMixin,
    TimestampMixin,
    db,
    isoformat,
    json_type,
    normalize_project_role,
    role_permission_defaults,
    safe_bool,
    safe_dict,
    safe_int,
    safe_str,
    utcnow,
)


PROJECT_ROLE_OWNER = "owner"
PROJECT_ROLE_ADMIN = "admin"
PROJECT_ROLE_EDITOR = "editor"
PROJECT_ROLE_VIEWER = "viewer"

PROJECT_ROLES = {
    PROJECT_ROLE_OWNER,
    PROJECT_ROLE_ADMIN,
    PROJECT_ROLE_EDITOR,
    PROJECT_ROLE_VIEWER,
}

PROJECT_PERMISSIONS = {
    "view",
    "edit",
    "manage",
    "delete",
    "transfer",
    "embed",
}

PERMISSION_FIELD_MAP = {
    "view": "can_view",
    "read": "can_view",
    "edit": "can_edit",
    "write": "can_edit",
    "manage": "can_manage",
    "admin": "can_manage",
    "delete": "can_delete",
    "remove": "can_delete",
    "transfer": "can_transfer",
    "owner_transfer": "can_transfer",
    "embed": "can_embed",
    "iframe": "can_embed",
}


# ─────────────────────────────────────────────────────────────
# Transitional import helpers
# ─────────────────────────────────────────────────────────────

def _metadata_has_table(table_name: str) -> bool:
    try:
        return str(table_name) in db.metadata.tables
    except Exception:
        return False


def _table_args(extend_existing: bool, *constraints: Any) -> Any:
    try:
        options = {"extend_existing": True} if extend_existing else {}

        if constraints:
            if options:
                return (*constraints, options)
            return constraints

        return options

    except Exception:
        return {"extend_existing": True} if extend_existing else {}


def _core_model_if_registered(model_name: str, table_name: str) -> Any:
    """
    Transitional guard.

    Current transition:
    - models/core.py may already have registered ProjectMembership.
    - This module must not register a duplicate table/class.
    - Once core.py becomes an aggregator, this module owns the model.
    """
    try:
        if not _metadata_has_table(table_name):
            return None

        try:
            from . import core as core_module

            model = getattr(core_module, model_name, None)
            if model is not None:
                return model
        except Exception:
            return None

    except Exception:
        return None

    return None


def _resolve_model(model_name: str, table_name: str, factory: Any) -> Any:
    try:
        existing = _core_model_if_registered(model_name, table_name)
        if existing is not None:
            return existing

        return factory(extend_existing=_metadata_has_table(table_name))

    except Exception:
        return factory(extend_existing=True)


# ─────────────────────────────────────────────────────────────
# Permission helpers
# ─────────────────────────────────────────────────────────────

def normalize_permission(value: Any, default: str = "view") -> str:
    try:
        text = safe_str(value, default, 80).strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "read": "view",
            "viewer": "view",
            "see": "view",
            "write": "edit",
            "editor": "edit",
            "modify": "edit",
            "admin": "manage",
            "manager": "manage",
            "owner": "manage",
            "remove": "delete",
            "destroy": "delete",
            "handover": "transfer",
            "iframe": "embed",
            "frame": "embed",
        }

        normalized = aliases.get(text, text)

        if normalized not in PROJECT_PERMISSIONS:
            return default

        return normalized

    except Exception:
        return default


def permission_field(permission: Any) -> str:
    try:
        normalized = normalize_permission(permission)
        return PERMISSION_FIELD_MAP.get(normalized, "can_view")
    except Exception:
        return "can_view"


def normalize_permissions(payload: Optional[Mapping[str, Any]] = None) -> Dict[str, bool]:
    try:
        data = safe_dict(payload)
        result: Dict[str, bool] = {}

        for permission in PROJECT_PERMISSIONS:
            field = permission_field(permission)
            value = data.get(permission, data.get(field, False))
            result[permission] = safe_bool(value, False)

        return result

    except Exception:
        return {
            "view": False,
            "edit": False,
            "manage": False,
            "delete": False,
            "transfer": False,
            "embed": False,
        }


def permissions_from_membership(membership: Any) -> Dict[str, bool]:
    try:
        if membership is None:
            return normalize_permissions({})

        return {
            "view": safe_bool(getattr(membership, "can_view", False), False),
            "edit": safe_bool(getattr(membership, "can_edit", False), False),
            "manage": safe_bool(getattr(membership, "can_manage", False), False),
            "delete": safe_bool(getattr(membership, "can_delete", False), False),
            "transfer": safe_bool(getattr(membership, "can_transfer", False), False),
            "embed": safe_bool(getattr(membership, "can_embed", False), False),
        }

    except Exception:
        return normalize_permissions({})


# ─────────────────────────────────────────────────────────────
# ProjectMembership model
# ─────────────────────────────────────────────────────────────

def _define_project_membership_model(*, extend_existing: bool = False):
    class ProjectMembership(TimestampMixin, SerializationMixin, db.Model):
        """
        Project membership and permission override.

        Roles:
        - owner
        - admin
        - editor
        - viewer

        Permissions are stored explicitly to allow future overrides without
        changing role names. The app's current placeholder user id=1 should
        receive an owner membership for projects it creates.
        """

        __tablename__ = "project_memberships"
        __table_args__ = _table_args(
            extend_existing,
            db.UniqueConstraint("project_id", "user_id", name="uq_project_memberships_project_user"),
        )

        id = db.Column(db.Integer, primary_key=True)

        project_id = db.Column(db.Integer, nullable=False, index=True)
        user_id = db.Column(db.Integer, nullable=False, index=True)

        role = db.Column(db.String(40), nullable=False, default=PROJECT_ROLE_VIEWER, index=True)

        can_view = db.Column(db.Boolean, nullable=False, default=True, index=True)
        can_edit = db.Column(db.Boolean, nullable=False, default=False, index=True)
        can_manage = db.Column(db.Boolean, nullable=False, default=False, index=True)
        can_delete = db.Column(db.Boolean, nullable=False, default=False, index=True)
        can_transfer = db.Column(db.Boolean, nullable=False, default=False, index=True)
        can_embed = db.Column(db.Boolean, nullable=False, default=False, index=True)

        status = db.Column(db.String(40), nullable=False, default="active", index=True)

        invited_by_user_id = db.Column(db.Integer, nullable=True, index=True)
        invited_at = db.Column(db.DateTime, nullable=True, index=True)
        accepted_at = db.Column(db.DateTime, nullable=True, index=True)

        revoked_by_user_id = db.Column(db.Integer, nullable=True, index=True)
        revoked_at = db.Column(db.DateTime, nullable=True, index=True)
        revoke_reason = db.Column(db.Text, nullable=True)

        expires_at = db.Column(db.DateTime, nullable=True, index=True)

        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        def __repr__(self) -> str:
            try:
                return (
                    f"<ProjectMembership project_id={self.project_id!r} "
                    f"user_id={self.user_id!r} role={self.role!r}>"
                )
            except Exception:
                return "<ProjectMembership>"

        @property
        def is_owner(self) -> bool:
            try:
                return normalize_project_role(self.role) == PROJECT_ROLE_OWNER
            except Exception:
                return False

        @property
        def is_admin(self) -> bool:
            try:
                return normalize_project_role(self.role) in {PROJECT_ROLE_OWNER, PROJECT_ROLE_ADMIN}
            except Exception:
                return False

        @property
        def is_active(self) -> bool:
            try:
                if self.revoked_at is not None:
                    return False

                if str(self.status or "").lower() not in {"active", "accepted", "enabled"}:
                    return False

                if self.expires_at is not None and self.expires_at <= utcnow():
                    return False

                return True

            except Exception:
                return False

        @property
        def permissions(self) -> Dict[str, bool]:
            return permissions_from_membership(self)

        def normalize(self) -> "ProjectMembership":
            try:
                self.project_id = safe_int(self.project_id, 0, minimum=1)
                self.user_id = safe_int(self.user_id, 0, minimum=1)
                self.role = normalize_project_role(self.role, PROJECT_ROLE_VIEWER)

                defaults = role_permission_defaults(self.role)

                self.can_view = safe_bool(self.can_view, defaults.get("view", True))
                self.can_edit = safe_bool(self.can_edit, defaults.get("edit", False))
                self.can_manage = safe_bool(self.can_manage, defaults.get("manage", False))
                self.can_delete = safe_bool(self.can_delete, defaults.get("delete", False))
                self.can_transfer = safe_bool(self.can_transfer, defaults.get("transfer", False))
                self.can_embed = safe_bool(self.can_embed, defaults.get("embed", False))

                if self.role == PROJECT_ROLE_OWNER:
                    self.can_view = True
                    self.can_edit = True
                    self.can_manage = True
                    self.can_delete = True
                    self.can_transfer = True
                    self.can_embed = True

                self.status = safe_str(self.status, "active", 40) or "active"
                self.invited_by_user_id = safe_int(self.invited_by_user_id, 0) or None
                self.revoked_by_user_id = safe_int(self.revoked_by_user_id, 0) or None
                self.revoke_reason = safe_str(self.revoke_reason, "", 2000) or None
                self.metadata_json = safe_dict(self.metadata_json)

                return self

            except Exception:
                return self

        def apply_role(
            self,
            role: Any,
            *,
            permissions: Optional[Mapping[str, Any]] = None,
            preserve_custom: bool = False,
        ) -> "ProjectMembership":
            try:
                normalized_role = normalize_project_role(role, PROJECT_ROLE_VIEWER)
                self.role = normalized_role

                if not preserve_custom:
                    defaults = role_permission_defaults(normalized_role)
                    self.can_view = bool(defaults.get("view", False))
                    self.can_edit = bool(defaults.get("edit", False))
                    self.can_manage = bool(defaults.get("manage", False))
                    self.can_delete = bool(defaults.get("delete", False))
                    self.can_transfer = bool(defaults.get("transfer", False))
                    self.can_embed = bool(defaults.get("embed", False))

                if permissions:
                    self.apply_permissions(permissions)

                self.status = "active"
                self.revoked_at = None
                self.revoked_by_user_id = None
                self.revoke_reason = None

                self.normalize()
                self.touch()

                return self

            except Exception:
                return self

        def apply_permissions(self, permissions: Optional[Mapping[str, Any]] = None) -> "ProjectMembership":
            try:
                data = safe_dict(permissions)

                for permission in PROJECT_PERMISSIONS:
                    field = permission_field(permission)

                    if permission in data:
                        setattr(self, field, safe_bool(data.get(permission), False))
                    elif field in data:
                        setattr(self, field, safe_bool(data.get(field), False))

                if self.role == PROJECT_ROLE_OWNER:
                    self.can_view = True
                    self.can_edit = True
                    self.can_manage = True
                    self.can_delete = True
                    self.can_transfer = True
                    self.can_embed = True

                self.touch()
                return self

            except Exception:
                return self

        def has_permission(self, permission: Any) -> bool:
            try:
                if not self.is_active:
                    return False

                normalized = normalize_permission(permission)
                field = permission_field(normalized)

                if self.role == PROJECT_ROLE_OWNER:
                    return True

                return safe_bool(getattr(self, field, False), False)

            except Exception:
                return False

        def can(self, permission: Any) -> bool:
            return self.has_permission(permission)

        def grant(self, permission: Any) -> None:
            try:
                field = permission_field(permission)
                setattr(self, field, True)
                self.touch()
            except Exception:
                pass

        def revoke_permission(self, permission: Any) -> None:
            try:
                if self.role == PROJECT_ROLE_OWNER:
                    return

                field = permission_field(permission)
                setattr(self, field, False)
                self.touch()
            except Exception:
                pass

        def accept(self) -> None:
            try:
                self.status = "active"
                self.accepted_at = self.accepted_at or utcnow()
                self.revoked_at = None
                self.revoked_by_user_id = None
                self.revoke_reason = None
                self.touch()
            except Exception:
                pass

        def revoke(self, *, user_id: Optional[int] = None, reason: str = "") -> None:
            try:
                self.status = "revoked"
                self.revoked_at = utcnow()
                self.revoked_by_user_id = safe_int(user_id, 0) or None
                self.revoke_reason = safe_str(reason, "", 2000) or None
                self.touch()
            except Exception:
                pass

        def restore(self) -> None:
            try:
                self.status = "active"
                self.revoked_at = None
                self.revoked_by_user_id = None
                self.revoke_reason = None
                self.touch()
            except Exception:
                pass

        def to_dict(self, *, include_private: bool = False) -> Dict[str, Any]:
            try:
                payload = {
                    "id": self.id,
                    "project_id": self.project_id,
                    "user_id": self.user_id,
                    "role": normalize_project_role(self.role, PROJECT_ROLE_VIEWER),
                    "is_owner": self.is_owner,
                    "is_admin": self.is_admin,
                    "is_active": self.is_active,
                    "status": self.status,
                    "permissions": self.permissions,
                    "can_view": bool(self.can_view),
                    "can_edit": bool(self.can_edit),
                    "can_manage": bool(self.can_manage),
                    "can_delete": bool(self.can_delete),
                    "can_transfer": bool(self.can_transfer),
                    "can_embed": bool(self.can_embed),
                    "invited_by_user_id": self.invited_by_user_id,
                    "invited_at": isoformat(self.invited_at),
                    "accepted_at": isoformat(self.accepted_at),
                    "revoked_at": isoformat(self.revoked_at),
                    "expires_at": isoformat(self.expires_at),
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                }

                if include_private:
                    payload["revoked_by_user_id"] = self.revoked_by_user_id
                    payload["revoke_reason"] = self.revoke_reason
                    payload["metadata"] = safe_dict(self.metadata_json)

                return payload

            except Exception:
                return {
                    "id": getattr(self, "id", None),
                    "project_id": getattr(self, "project_id", None),
                    "user_id": getattr(self, "user_id", None),
                    "role": getattr(self, "role", PROJECT_ROLE_VIEWER),
                }

        @classmethod
        def build(
            cls,
            *,
            project_id: Any,
            user_id: Any,
            role: Any = PROJECT_ROLE_VIEWER,
            permissions: Optional[Mapping[str, Any]] = None,
            invited_by_user_id: Optional[int] = None,
            status: str = "active",
            metadata: Optional[Mapping[str, Any]] = None,
        ) -> "ProjectMembership":
            membership = cls()
            membership.project_id = safe_int(project_id, 0, minimum=1)
            membership.user_id = safe_int(user_id, 0, minimum=1)
            membership.role = normalize_project_role(role, PROJECT_ROLE_VIEWER)
            membership.status = safe_str(status, "active", 40) or "active"
            membership.invited_by_user_id = safe_int(invited_by_user_id, 0) or None
            membership.invited_at = utcnow() if invited_by_user_id else None
            membership.accepted_at = utcnow() if membership.status in {"active", "accepted"} else None
            membership.metadata_json = safe_dict(metadata)

            membership.apply_role(membership.role, permissions=permissions, preserve_custom=False)
            membership.normalize()

            return membership

    return ProjectMembership


ProjectMembership = _resolve_model(
    "ProjectMembership",
    "project_memberships",
    _define_project_membership_model,
)


# ─────────────────────────────────────────────────────────────
# Convenience helpers
# ─────────────────────────────────────────────────────────────

def build_membership(
    *,
    project_id: Any,
    user_id: Any,
    role: Any = PROJECT_ROLE_VIEWER,
    permissions: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
) -> ProjectMembership:
    try:
        if hasattr(ProjectMembership, "build"):
            return ProjectMembership.build(
                project_id=project_id,
                user_id=user_id,
                role=role,
                permissions=permissions,
                **kwargs,
            )

        membership = ProjectMembership()
        membership.project_id = safe_int(project_id, 0, minimum=1)
        membership.user_id = safe_int(user_id, 0, minimum=1)
        membership.role = normalize_project_role(role, PROJECT_ROLE_VIEWER)

        if hasattr(membership, "apply_role"):
            membership.apply_role(role, permissions=permissions)
        elif hasattr(membership, "normalize"):
            membership.normalize()

        return membership

    except Exception:
        membership = ProjectMembership()
        try:
            membership.project_id = safe_int(project_id, 0)
            membership.user_id = safe_int(user_id, 0)
            membership.role = normalize_project_role(role, PROJECT_ROLE_VIEWER)
        except Exception:
            pass
        return membership


def get_project_membership(project_id: Any, user_id: Any) -> Optional[ProjectMembership]:
    try:
        resolved_project_id = safe_int(project_id, 0, minimum=1)
        resolved_user_id = safe_int(user_id, 0, minimum=1)

        if not resolved_project_id or not resolved_user_id:
            return None

        return ProjectMembership.query.filter_by(
            project_id=resolved_project_id,
            user_id=resolved_user_id,
        ).one_or_none()

    except Exception:
        return None


def list_project_memberships(project_id: Any, *, active_only: bool = False) -> List[ProjectMembership]:
    try:
        resolved_project_id = safe_int(project_id, 0, minimum=1)

        if not resolved_project_id:
            return []

        query = ProjectMembership.query.filter_by(project_id=resolved_project_id)

        if active_only:
            query = query.filter(ProjectMembership.revoked_at.is_(None))

        return list(query.order_by(ProjectMembership.role.asc(), ProjectMembership.user_id.asc()).all())

    except Exception:
        return []


def serialize_membership(membership: Any, *, include_private: bool = False) -> Dict[str, Any]:
    try:
        if membership is None:
            return {}

        if hasattr(membership, "to_dict"):
            try:
                return membership.to_dict(include_private=include_private)
            except TypeError:
                return membership.to_dict()

        return {
            "id": getattr(membership, "id", None),
            "project_id": getattr(membership, "project_id", None),
            "user_id": getattr(membership, "user_id", None),
            "role": getattr(membership, "role", PROJECT_ROLE_VIEWER),
            "permissions": permissions_from_membership(membership),
        }

    except Exception:
        return {}


def serialize_memberships(memberships: Any, *, include_private: bool = False) -> List[Dict[str, Any]]:
    try:
        return [
            serialize_membership(item, include_private=include_private)
            for item in list(memberships or [])
            if item is not None
        ]
    except Exception:
        return []


def ensure_owner_membership(
    *,
    project_id: Any,
    owner_user_id: Any,
    commit: bool = True,
) -> ProjectMembership:
    resolved_project_id = safe_int(project_id, 0, minimum=1)
    resolved_user_id = safe_int(owner_user_id, 0, minimum=1)

    try:
        membership = get_project_membership(resolved_project_id, resolved_user_id)

        if membership is None:
            membership = build_membership(
                project_id=resolved_project_id,
                user_id=resolved_user_id,
                role=PROJECT_ROLE_OWNER,
            )
            db.session.add(membership)
        else:
            if hasattr(membership, "apply_role"):
                membership.apply_role(PROJECT_ROLE_OWNER)
            else:
                membership.role = PROJECT_ROLE_OWNER
                membership.can_view = True
                membership.can_edit = True
                membership.can_manage = True
                membership.can_delete = True
                membership.can_transfer = True
                membership.can_embed = True

            db.session.add(membership)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return membership

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

        return build_membership(
            project_id=resolved_project_id,
            user_id=resolved_user_id,
            role=PROJECT_ROLE_OWNER,
        )


def get_project_access_model_classes() -> List[Any]:
    return [ProjectMembership]


def get_project_access_model_status() -> Dict[str, Any]:
    try:
        count = -1

        try:
            count = int(ProjectMembership.query.count())
        except Exception:
            count = -1

        return {
            "ok": True,
            "models": ["ProjectMembership"],
            "tables": [getattr(ProjectMembership, "__tablename__", "project_memberships")],
            "count": count,
            "roles": sorted(PROJECT_ROLES),
            "permissions": sorted(PROJECT_PERMISSIONS),
        }

    except Exception as exc:
        return {
            "ok": False,
            "models": ["ProjectMembership"],
            "tables": ["project_memberships"],
            "error": str(exc),
        }


ProjectAccess = ProjectMembership
ProjectPermission = ProjectMembership


__all__ = [
    "PROJECT_ROLE_OWNER",
    "PROJECT_ROLE_ADMIN",
    "PROJECT_ROLE_EDITOR",
    "PROJECT_ROLE_VIEWER",
    "PROJECT_ROLES",
    "PROJECT_PERMISSIONS",
    "PERMISSION_FIELD_MAP",
    "ProjectMembership",
    "ProjectAccess",
    "ProjectPermission",
    "normalize_permission",
    "permission_field",
    "normalize_permissions",
    "permissions_from_membership",
    "build_membership",
    "get_project_membership",
    "list_project_memberships",
    "serialize_membership",
    "serialize_memberships",
    "ensure_owner_membership",
    "get_project_access_model_classes",
    "get_project_access_model_status",
]