# services/vectoplan-app/services/project_access_context.py
from __future__ import annotations

import copy
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


try:
    from flask import current_app, has_app_context, has_request_context, request
except Exception:  # pragma: no cover - service must be importable outside Flask
    current_app = None  # type: ignore[assignment]

    def has_app_context() -> bool:  # type: ignore[override]
        return False

    def has_request_context() -> bool:  # type: ignore[override]
        return False

    request = None  # type: ignore[assignment]


try:
    from services.project_permissions import (
        PERMISSION_VIEW,
        PermissionDenied,
        require_project_permission,
        serialize_project_permissions,
    )
except Exception:  # pragma: no cover
    PERMISSION_VIEW = "view"  # type: ignore
    PermissionDenied = RuntimeError  # type: ignore
    require_project_permission = None  # type: ignore
    serialize_project_permissions = None  # type: ignore


try:
    from services.project_publication_service import get_project_publication
except Exception:  # pragma: no cover
    get_project_publication = None  # type: ignore


logger = logging.getLogger(__name__)


DEFAULT_CACHE_TTL_SECONDS = 5
DEFAULT_SOURCE = "project_access_context"

TRUTHY_VALUES = {"1", "true", "yes", "y", "on", "enabled", "active", "ok", "ja"}
FALSY_VALUES = {"0", "false", "no", "n", "off", "disabled", "inactive", "failed", "nein", ""}

ACTION_TO_PERMISSION = {
    "view": "view",
    "read": "view",
    "open": "view",
    "embed": "embed",
    "workspace": "view",
    "edit": "edit",
    "update": "edit",
    "manage": "manage",
    "delete": "delete",
    "transfer": "transfer",
}

PUBLIC_READ_ACTIONS = {"view", "read", "open", "embed", "workspace"}

PUBLIC_WORKSPACES = {
    "project",
    "map",
    "editor3d",
    "cad2d",
    "lv",
    "files",
    "structural_calculation",
    "energy_calculation",
    "sound_protection_calculation",
}

NEVER_PUBLIC_WORKSPACES = {
    "admin",
    "team",
    "settings",
    "permissions",
    "system",
    "system_refs",
    "admin_settings",
    "project_admin",
}

WORKSPACE_ALIASES = {
    "": "project",
    "info": "project",
    "overview": "project",
    "projekt": "project",
    "project": "project",
    "map": "map",
    "karte": "map",
    "openlayer": "map",
    "3d": "editor3d",
    "editor": "editor3d",
    "editor3d": "editor3d",
    "viewer": "editor3d",
    "viewer3d": "editor3d",
    "2d": "cad2d",
    "cad": "cad2d",
    "cad2d": "cad2d",
    "plan": "cad2d",
    "lv": "lv",
    "boq": "lv",
    "leistungsverzeichnis": "lv",
    "files": "files",
    "dateien": "files",
    "filecloud": "files",
    "structural_calculation": "structural_calculation",
    "tragwerksberechnung": "structural_calculation",
    "statik": "structural_calculation",
    "energy_calculation": "energy_calculation",
    "energieberechnung": "energy_calculation",
    "energie": "energy_calculation",
    "sound_protection_calculation": "sound_protection_calculation",
    "schallschutzberechnung": "sound_protection_calculation",
    "schallschutz": "sound_protection_calculation",
    "versions": "versions",
    "versionen": "versions",
    "history": "versions",
    "admin": "admin",
    "team": "team",
    "settings": "settings",
    "permissions": "permissions",
    "system": "system",
}


@dataclass
class PublicAccessEvaluation:
    allowed: bool = False
    status_code: int = 403
    code: str = "public_access_denied"
    message: str = "Öffentlicher Zugriff ist nicht erlaubt."
    visibility: str = "private"
    publication_enabled: bool = False
    workspace_published: bool = False
    require_auth: bool = True
    require_project_permission: bool = True
    workspace: str = "project"
    publication: Dict[str, Any] = field(default_factory=dict)
    reason: str = "not_public"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status_code": self.status_code,
            "code": self.code,
            "message": self.message,
            "visibility": self.visibility,
            "publication_enabled": self.publication_enabled,
            "workspace_published": self.workspace_published,
            "require_auth": self.require_auth,
            "requireAuth": self.require_auth,
            "require_project_permission": self.require_project_permission,
            "requireProjectPermission": self.require_project_permission,
            "workspace": self.workspace,
            "publication": copy.deepcopy(self.publication),
            "reason": self.reason,
        }


@dataclass
class ProjectAccessContext:
    ok: bool
    allowed: bool
    access_mode: str
    code: str
    message: str
    status_code: int
    workspace: str = "project"
    action: str = "view"
    source: str = DEFAULT_SOURCE

    read_only: bool = True
    public_viewer: bool = False
    demo_mode: bool = False
    authenticated: bool = False
    persistent: bool = False

    user_id: Optional[int] = None
    auth_user_id: str = ""

    project_id: Optional[Any] = None
    project_public_id: str = ""
    project_is_demo: bool = False

    role: str = "viewer"
    permissions: Dict[str, bool] = field(default_factory=dict)

    visibility: str = "private"
    publication_enabled: bool = False
    workspace_published: bool = False
    require_auth: bool = True
    require_project_permission: bool = True

    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    publication: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        permissions = dict(self.permissions or {})
        permissions.setdefault("view", bool(self.allowed))
        permissions.setdefault("edit", bool(self.allowed and not self.read_only and not self.public_viewer))
        permissions.setdefault("manage", False)
        permissions.setdefault("delete", False)
        permissions.setdefault("transfer", False)
        permissions.setdefault("embed", bool(self.allowed and (not self.public_viewer or self.workspace in PUBLIC_WORKSPACES)))
        permissions.setdefault("view_settings", bool(self.allowed and not self.public_viewer and not self.demo_mode))
        permissions.setdefault("manage_settings", False)
        permissions.setdefault("view_team", bool(self.allowed and not self.public_viewer and not self.demo_mode))
        permissions.setdefault("manage_team", False)
        permissions.setdefault("view_admin", bool(self.allowed and not self.public_viewer and not self.demo_mode))

        return {
            "ok": self.ok,
            "allowed": self.allowed,
            "access_mode": self.access_mode,
            "accessMode": self.access_mode,
            "code": self.code,
            "message": self.message,
            "status_code": self.status_code,
            "statusCode": self.status_code,
            "workspace": self.workspace,
            "action": self.action,
            "source": self.source,
            "reason": self.reason or self.code,
            "read_only": self.read_only,
            "readOnly": self.read_only,
            "public_viewer": self.public_viewer,
            "publicViewer": self.public_viewer,
            "is_public_viewer": self.public_viewer,
            "isPublicViewer": self.public_viewer,
            "demo_mode": self.demo_mode,
            "demoMode": self.demo_mode,
            "authenticated": self.authenticated,
            "persistent": self.persistent,
            "user_id": self.user_id,
            "userId": self.user_id,
            "auth_user_id": self.auth_user_id,
            "authUserId": self.auth_user_id,
            "project_id": self.project_id,
            "projectId": self.project_id,
            "project_public_id": self.project_public_id,
            "projectPublicId": self.project_public_id,
            "project_is_demo": self.project_is_demo,
            "projectIsDemo": self.project_is_demo,
            "role": self.role,
            "permissions": permissions,
            "can_view": bool(permissions.get("view")),
            "canView": bool(permissions.get("view")),
            "can_edit": bool(permissions.get("edit")),
            "canEdit": bool(permissions.get("edit")),
            "can_manage": bool(permissions.get("manage")),
            "canManage": bool(permissions.get("manage")),
            "can_delete": bool(permissions.get("delete")),
            "canDelete": bool(permissions.get("delete")),
            "can_transfer": bool(permissions.get("transfer")),
            "canTransfer": bool(permissions.get("transfer")),
            "can_embed": bool(permissions.get("embed")),
            "canEmbed": bool(permissions.get("embed")),
            "can_view_settings": bool(permissions.get("view_settings")),
            "canViewSettings": bool(permissions.get("view_settings")),
            "can_manage_settings": bool(permissions.get("manage_settings")),
            "canManageSettings": bool(permissions.get("manage_settings")),
            "can_view_team": bool(permissions.get("view_team")),
            "canViewTeam": bool(permissions.get("view_team")),
            "can_manage_team": bool(permissions.get("manage_team")),
            "canManageTeam": bool(permissions.get("manage_team")),
            "can_view_admin": bool(permissions.get("view_admin")),
            "canViewAdmin": bool(permissions.get("view_admin")),
            "visibility": self.visibility,
            "publication_enabled": self.publication_enabled,
            "publicationEnabled": self.publication_enabled,
            "workspace_published": self.workspace_published,
            "workspacePublished": self.workspace_published,
            "require_auth": self.require_auth,
            "requireAuth": self.require_auth,
            "require_project_permission": self.require_project_permission,
            "requireProjectPermission": self.require_project_permission,
            "publication": copy.deepcopy(self.publication or {}),
            "metadata": copy.deepcopy(self.metadata or {}),
        }


class _TTLCache:
    def __init__(self) -> None:
        self._items: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any:
        try:
            now = time.monotonic()
            with self._lock:
                item = self._items.get(key)
                if not item:
                    return None

                expires_at, value = item
                if expires_at <= now:
                    self._items.pop(key, None)
                    return None

                return copy.deepcopy(value)
        except Exception:
            logger.exception("project_access_context.cache_get_failed")
            return None

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        try:
            if ttl_seconds <= 0:
                return

            with self._lock:
                self._items[key] = (time.monotonic() + ttl_seconds, copy.deepcopy(value))
                self._prune_locked()
        except Exception:
            logger.exception("project_access_context.cache_set_failed")

    def clear(self) -> int:
        try:
            with self._lock:
                count = len(self._items)
                self._items.clear()
                return count
        except Exception:
            logger.exception("project_access_context.cache_clear_failed")
            return 0

    def status(self) -> Dict[str, Any]:
        try:
            with self._lock:
                self._prune_locked()
                return {
                    "ok": True,
                    "items": len(self._items),
                    "ttl_seconds": _cache_ttl_seconds(),
                }
        except Exception as exc:
            logger.exception("project_access_context.cache_status_failed")
            return {
                "ok": False,
                "items": None,
                "error": _safe_error(exc),
            }

    def _prune_locked(self) -> None:
        try:
            now = time.monotonic()
            expired = [key for key, (expires_at, _) in self._items.items() if expires_at <= now]
            for key in expired:
                self._items.pop(key, None)
        except Exception:
            logger.exception("project_access_context.cache_prune_failed")


_CACHE = _TTLCache()


def resolve_project_shell_access(
    project: Any,
    *,
    current_user_context: Any = None,
    action: str = "view",
    use_cache: bool = True,
) -> ProjectAccessContext:
    return resolve_project_access(
        project,
        current_user_context=current_user_context,
        workspace="project",
        action=action,
        use_cache=use_cache,
    )


def resolve_project_workspace_access(
    project: Any,
    *,
    workspace: str,
    current_user_context: Any = None,
    action: str = "view",
    use_cache: bool = True,
) -> ProjectAccessContext:
    return resolve_project_access(
        project,
        current_user_context=current_user_context,
        workspace=workspace,
        action=action,
        use_cache=use_cache,
    )


def resolve_project_access(
    project: Any,
    *,
    current_user_context: Any = None,
    workspace: str = "project",
    action: str = "view",
    use_cache: bool = True,
) -> ProjectAccessContext:
    """
    Central access resolver for project shell and project workspaces.

    Important rule:
      A non-authenticated demo-capable session is not automatically an active
      demo context for every project. Public projects must be checked before
      demo-mode denial is applied.

    Order:
      1. Technical auth outage / blocked user.
      2. Authenticated membership/owner permission for persistent projects.
      3. Public read-only fallback for public/unlisted published projects.
      4. Demo project access.
      5. Login/permission denial.
    """

    try:
        normalized_workspace = normalize_workspace(workspace)
        normalized_action = normalize_action(action)
        user = _safe_dict(current_user_context)
        project_public_id = _project_public_id(project, "")
        cache_key = _cache_key(
            project=project,
            current_user_context=user,
            workspace=normalized_workspace,
            action=normalized_action,
        )

        if use_cache:
            cached = _CACHE.get(cache_key)
            if isinstance(cached, ProjectAccessContext):
                return cached

        result = _resolve_project_access_uncached(
            project,
            current_user_context=user,
            workspace=normalized_workspace,
            action=normalized_action,
        )

        if use_cache and _result_cacheable(result):
            _CACHE.set(cache_key, result, _cache_ttl_seconds())

        return result

    except Exception as exc:
        logger.exception("project_access_context.resolve_failed")
        return ProjectAccessContext(
            ok=False,
            allowed=False,
            access_mode="error",
            code="project_access_context_failed",
            message="Projektzugriff konnte nicht geprüft werden.",
            status_code=500,
            workspace=normalize_workspace(workspace),
            action=normalize_action(action),
            source=DEFAULT_SOURCE,
            read_only=True,
            public_viewer=False,
            demo_mode=False,
            authenticated=False,
            persistent=False,
            project_id=_project_id(project),
            project_public_id=_project_public_id(project, ""),
            reason="resolver_exception",
            metadata={"error": _safe_error(exc)},
        )


def _resolve_project_access_uncached(
    project: Any,
    *,
    current_user_context: Mapping[str, Any],
    workspace: str,
    action: str,
) -> ProjectAccessContext:
    user = _safe_dict(current_user_context)
    project_id = _project_id(project)
    project_public_id = _project_public_id(project, "")
    project_is_demo = _project_is_demo(project)
    user_id = _current_user_id(user)
    auth_user_id = _auth_user_id(user)
    authenticated = _is_authenticated_context(user)
    persistent = _is_persistent_context(user)
    demo_mode = _is_demo_context(user)
    auth_unavailable = _is_auth_unavailable_context(user)
    user_blocked = _is_user_blocked_context(user)

    if project is None:
        return _new_project_access_context(
            user,
            workspace=workspace,
            action=action,
        )

    if auth_unavailable:
        return ProjectAccessContext(
            ok=False,
            allowed=False,
            access_mode="auth_unavailable",
            code=_context_code(user) or "auth_service_unavailable",
            message="vectoplan-auth ist nicht erreichbar.",
            status_code=503,
            workspace=workspace,
            action=action,
            read_only=True,
            public_viewer=False,
            demo_mode=False,
            authenticated=False,
            persistent=False,
            user_id=None,
            auth_user_id=auth_user_id,
            project_id=project_id,
            project_public_id=project_public_id,
            project_is_demo=project_is_demo,
            role="none",
            permissions=_permissions_none(),
            reason="auth_unavailable",
            metadata={"blocked_kind": "auth_unavailable"},
        )

    if user_blocked:
        return ProjectAccessContext(
            ok=False,
            allowed=False,
            access_mode="blocked",
            code=_context_code(user) or "auth_blocked",
            message="Dieser Zugang ist gesperrt.",
            status_code=403,
            workspace=workspace,
            action=action,
            read_only=True,
            public_viewer=False,
            demo_mode=False,
            authenticated=authenticated,
            persistent=False,
            user_id=user_id,
            auth_user_id=auth_user_id,
            project_id=project_id,
            project_public_id=project_public_id,
            project_is_demo=project_is_demo,
            role="none",
            permissions=_permissions_none(),
            reason="user_blocked",
            metadata={"blocked_kind": "user_blocked"},
        )

    public_eval = evaluate_public_project_access(
        project,
        workspace=workspace,
        action=action,
        current_user_context=user,
        use_cache=True,
    )

    # A published project remains editable for its owner and members. Public
    # access is only a fallback for actors without project permissions.
    member_permission_result: Optional[_MemberPermissionEvaluation] = None
    if authenticated and user_id and not project_is_demo:
        member_permission_result = _evaluate_member_permission(
            project,
            user_id=user_id,
            workspace=workspace,
            action=action,
        )

        if member_permission_result.allowed:
            permissions = member_permission_result.permissions or _permissions_view_only()
            read_only = not bool(permissions.get("edit") or permissions.get("manage"))
            return ProjectAccessContext(
                ok=True,
                allowed=True,
                access_mode="authenticated",
                code="project_permission_allowed",
                message="Projektzugriff erlaubt.",
                status_code=200,
                workspace=workspace,
                action=action,
                read_only=read_only,
                public_viewer=False,
                demo_mode=False,
                authenticated=True,
                persistent=persistent,
                user_id=user_id,
                auth_user_id=auth_user_id,
                project_id=project_id,
                project_public_id=project_public_id,
                project_is_demo=False,
                role=member_permission_result.role or "member",
                permissions=permissions,
                visibility=public_eval.visibility,
                publication_enabled=public_eval.publication_enabled,
                workspace_published=public_eval.workspace_published,
                require_auth=public_eval.require_auth,
                require_project_permission=public_eval.require_project_permission,
                publication=public_eval.publication,
                source=DEFAULT_SOURCE,
                reason=member_permission_result.reason or "member_permission_allowed",
                metadata=member_permission_result.metadata,
            )

    if public_eval.allowed:
        permissions = _permissions_public()
        return ProjectAccessContext(
            ok=True,
            allowed=True,
            access_mode="public",
            code="public_project_allowed",
            message="Öffentlicher Projektzugriff erlaubt.",
            status_code=200,
            workspace=workspace,
            action=action,
            read_only=True,
            public_viewer=True,
            demo_mode=False,
            authenticated=authenticated,
            persistent=False,
            user_id=user_id if authenticated else None,
            auth_user_id=auth_user_id,
            project_id=project_id,
            project_public_id=project_public_id,
            project_is_demo=False,
            role="public_viewer",
            permissions=permissions,
            visibility=public_eval.visibility,
            publication_enabled=public_eval.publication_enabled,
            workspace_published=public_eval.workspace_published,
            require_auth=public_eval.require_auth,
            require_project_permission=public_eval.require_project_permission,
            source=DEFAULT_SOURCE,
            reason=public_eval.reason or "public_allowed",
            publication=public_eval.publication,
            metadata={
                "public_access_checked_before_demo_deny": True,
                "public_evaluation": public_eval.to_dict(),
            },
        )

    if project_is_demo:
        if demo_mode:
            permissions = _permissions_demo()
            return ProjectAccessContext(
                ok=True,
                allowed=True,
                access_mode="demo",
                code="demo_project_allowed",
                message="Demo-Projektzugriff erlaubt.",
                status_code=200,
                workspace=workspace,
                action=action,
                read_only=False,
                public_viewer=False,
                demo_mode=True,
                authenticated=False,
                persistent=False,
                user_id=None,
                auth_user_id=auth_user_id,
                project_id=project_id,
                project_public_id=project_public_id,
                project_is_demo=True,
                role="demo_editor",
                permissions=permissions,
                visibility="private",
                publication_enabled=False,
                workspace_published=False,
                require_auth=False,
                require_project_permission=False,
                source=DEFAULT_SOURCE,
                reason="demo_project",
                metadata={
                    "demo_project": True,
                    "temporary": True,
                },
            )

        return ProjectAccessContext(
            ok=False,
            allowed=False,
            access_mode="anonymous",
            code="demo_project_requires_demo_context",
            message="Dieses Demo-Projekt ist nur im Demo-Modus verfügbar.",
            status_code=403,
            workspace=workspace,
            action=action,
            read_only=True,
            public_viewer=False,
            demo_mode=False,
            authenticated=authenticated,
            persistent=persistent,
            user_id=user_id,
            auth_user_id=auth_user_id,
            project_id=project_id,
            project_public_id=project_public_id,
            project_is_demo=True,
            role="none",
            permissions=_permissions_none(),
            reason="demo_project_requires_demo_context",
        )

    if demo_mode and not public_eval.allowed:
        return ProjectAccessContext(
            ok=False,
            allowed=False,
            access_mode="demo",
            code="demo_mode_not_allowed",
            message="Im Demo-Modus ist diese Projektaktion nicht erlaubt.",
            status_code=403,
            workspace=workspace,
            action=action,
            read_only=True,
            public_viewer=False,
            demo_mode=True,
            authenticated=False,
            persistent=False,
            user_id=None,
            auth_user_id=auth_user_id,
            project_id=project_id,
            project_public_id=project_public_id,
            project_is_demo=False,
            role="none",
            permissions=_permissions_none(),
            visibility=public_eval.visibility,
            publication_enabled=public_eval.publication_enabled,
            workspace_published=public_eval.workspace_published,
            require_auth=public_eval.require_auth,
            require_project_permission=public_eval.require_project_permission,
            publication=public_eval.publication,
            source=DEFAULT_SOURCE,
            reason=public_eval.reason or "demo_mode_not_allowed_for_private_project",
            metadata={
                "public_access_checked": True,
                "public_access_denial": public_eval.to_dict(),
            },
        )

    if authenticated and user_id:
        permission_result = member_permission_result or _evaluate_member_permission(
            project,
            user_id=user_id,
            workspace=workspace,
            action=action,
        )
        return ProjectAccessContext(
            ok=False,
            allowed=False,
            access_mode="authenticated",
            code=permission_result.code or "project_permission_denied",
            message=permission_result.message or "Zugriff verweigert.",
            status_code=permission_result.status_code or 403,
            workspace=workspace,
            action=action,
            read_only=True,
            public_viewer=False,
            demo_mode=False,
            authenticated=True,
            persistent=persistent,
            user_id=user_id,
            auth_user_id=auth_user_id,
            project_id=project_id,
            project_public_id=project_public_id,
            project_is_demo=False,
            role="none",
            permissions=_permissions_none(),
            visibility=public_eval.visibility,
            publication_enabled=public_eval.publication_enabled,
            workspace_published=public_eval.workspace_published,
            require_auth=public_eval.require_auth,
            require_project_permission=public_eval.require_project_permission,
            publication=public_eval.publication,
            source=DEFAULT_SOURCE,
            reason=permission_result.reason or "member_permission_denied",
            metadata=permission_result.metadata,
        )

    return ProjectAccessContext(
        ok=False,
        allowed=False,
        access_mode="anonymous",
        code=_anonymous_denial_code(public_eval),
        message=_anonymous_denial_message(public_eval),
        status_code=_anonymous_denial_status(public_eval),
        workspace=workspace,
        action=action,
        read_only=True,
        public_viewer=False,
        demo_mode=False,
        authenticated=False,
        persistent=False,
        user_id=None,
        auth_user_id=auth_user_id,
        project_id=project_id,
        project_public_id=project_public_id,
        project_is_demo=False,
        role="none",
        permissions=_permissions_none(),
        visibility=public_eval.visibility,
        publication_enabled=public_eval.publication_enabled,
        workspace_published=public_eval.workspace_published,
        require_auth=public_eval.require_auth,
        require_project_permission=public_eval.require_project_permission,
        publication=public_eval.publication,
        source=DEFAULT_SOURCE,
        reason=public_eval.reason or "anonymous_access_denied",
        metadata={
            "public_access_checked": True,
            "public_access_denial": public_eval.to_dict(),
        },
    )


@dataclass
class _MemberPermissionEvaluation:
    allowed: bool = False
    status_code: int = 403
    code: str = "project_permission_denied"
    message: str = "Zugriff verweigert."
    role: str = ""
    permissions: Dict[str, bool] = field(default_factory=dict)
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


def _evaluate_member_permission(
    project: Any,
    *,
    user_id: int,
    workspace: str,
    action: str,
) -> _MemberPermissionEvaluation:
    try:
        permission = ACTION_TO_PERMISSION.get(action, "view")
        if permission == "view":
            permission = PERMISSION_VIEW

        if serialize_project_permissions is not None:
            try:
                serialized = _safe_dict(serialize_project_permissions(project, user_id=user_id))
                permissions = _permissions_from_payload(serialized)
                role = _safe_str(serialized.get("role"), "", 80)
                can_view = _safe_bool(
                    serialized.get("can_view")
                    or serialized.get("canView")
                    or permissions.get("view"),
                    False,
                )

                if can_view:
                    required_allowed = True
                    if permission and permission != "view":
                        required_allowed = bool(permissions.get(permission))

                    if required_allowed or permission == "embed":
                        return _MemberPermissionEvaluation(
                            allowed=True,
                            status_code=200,
                            code="project_permission_allowed",
                            message="Projektzugriff erlaubt.",
                            role=role or "member",
                            permissions=permissions,
                            reason="serialized_permissions_allowed",
                            metadata={"serialized": _sanitized_permission_payload(serialized)},
                        )
            except Exception:
                logger.debug("project_access_context.serialize_permissions_failed", exc_info=True)

        if require_project_permission is not None:
            try:
                result = require_project_permission(
                    project,
                    permission,
                    user_id,
                    allow_public_view=False,
                )

                if result is False:
                    return _MemberPermissionEvaluation(
                        allowed=False,
                        status_code=403,
                        code="project_permission_denied",
                        message="Zugriff verweigert.",
                        reason="require_project_permission_false",
                    )

                data = _safe_dict(result)
                if data and data.get("ok") is False:
                    return _MemberPermissionEvaluation(
                        allowed=False,
                        status_code=_safe_int(data.get("status_code"), 403),
                        code=_safe_str(data.get("code"), "project_permission_denied", 120),
                        message=_safe_str(data.get("message") or data.get("error"), "Zugriff verweigert.", 500),
                        reason="require_project_permission_denied",
                        metadata={"permission_result": _sanitize_dict(data)},
                    )

                permissions = _permissions_from_payload(data)
                if not permissions:
                    permissions = _permissions_view_only()

                return _MemberPermissionEvaluation(
                    allowed=True,
                    status_code=200,
                    code="project_permission_allowed",
                    message="Projektzugriff erlaubt.",
                    role=_safe_str(data.get("role"), "member", 80) if data else "member",
                    permissions=permissions,
                    reason="require_project_permission_allowed",
                    metadata={"permission": permission},
                )

            except PermissionDenied as exc:  # type: ignore[misc]
                return _MemberPermissionEvaluation(
                    allowed=False,
                    status_code=_safe_int(getattr(exc, "status_code", None), 403),
                    code=_safe_str(getattr(exc, "code", None), "project_permission_denied", 120),
                    message=_safe_str(getattr(exc, "message", None) or str(exc), "Zugriff verweigert.", 500),
                    reason="permission_denied_exception",
                    metadata={"permission": permission},
                )
            except TypeError:
                try:
                    result = require_project_permission(project, permission, user_id)
                    if result is False:
                        return _MemberPermissionEvaluation(
                            allowed=False,
                            status_code=403,
                            code="project_permission_denied",
                            message="Zugriff verweigert.",
                            reason="require_project_permission_legacy_false",
                        )
                    return _MemberPermissionEvaluation(
                        allowed=True,
                        status_code=200,
                        code="project_permission_allowed",
                        message="Projektzugriff erlaubt.",
                        role="member",
                        permissions=_permissions_view_only(),
                        reason="require_project_permission_legacy_allowed",
                    )
                except Exception as exc:
                    logger.debug("project_access_context.legacy_permission_failed", exc_info=True)
                    return _MemberPermissionEvaluation(
                        allowed=False,
                        status_code=403,
                        code="project_permission_denied",
                        message="Zugriff verweigert.",
                        reason="legacy_permission_exception",
                        metadata={"error": _safe_error(exc)},
                    )

        owner_user_id = _safe_int(_safe_get(project, "owner_user_id"), 0)
        if owner_user_id > 0 and owner_user_id == user_id:
            return _MemberPermissionEvaluation(
                allowed=True,
                status_code=200,
                code="project_owner_allowed",
                message="Projektzugriff erlaubt.",
                role="owner",
                permissions=_permissions_owner(),
                reason="owner_user_id_match",
            )

        return _MemberPermissionEvaluation(
            allowed=False,
            status_code=403,
            code="project_permission_denied",
            message="Zugriff verweigert.",
            reason="no_permission_backend_available",
        )
    except Exception as exc:
        logger.exception("project_access_context.member_permission_failed")
        return _MemberPermissionEvaluation(
            allowed=False,
            status_code=500,
            code="project_permission_check_failed",
            message="Projektberechtigung konnte nicht geprüft werden.",
            reason="permission_exception",
            metadata={"error": _safe_error(exc)},
        )


def evaluate_public_project_access(
    project: Any,
    *,
    workspace: str = "project",
    action: str = "view",
    current_user_context: Any = None,
    use_cache: bool = True,
) -> PublicAccessEvaluation:
    try:
        normalized_workspace = normalize_workspace(workspace)
        normalized_action = normalize_action(action)

        cache_key = (
            "public:"
            + _project_public_id(project, "")
            + ":"
            + normalized_workspace
            + ":"
            + normalized_action
            + ":"
            + str(_publication_fallback_from_visibility_enabled())
        )

        if use_cache:
            cached = _CACHE.get(cache_key)
            if isinstance(cached, PublicAccessEvaluation):
                return cached

        result = _evaluate_public_project_access_uncached(
            project,
            workspace=normalized_workspace,
            action=normalized_action,
            current_user_context=current_user_context,
        )

        if use_cache:
            _CACHE.set(cache_key, result, _cache_ttl_seconds())

        return result
    except Exception as exc:
        logger.exception("project_access_context.public_access_failed")
        return PublicAccessEvaluation(
            allowed=False,
            status_code=500,
            code="public_access_check_failed",
            message="Öffentlicher Projektzugriff konnte nicht geprüft werden.",
            workspace=normalize_workspace(workspace),
            reason="exception",
            publication={},
        )


def _evaluate_public_project_access_uncached(
    project: Any,
    *,
    workspace: str,
    action: str,
    current_user_context: Any = None,
) -> PublicAccessEvaluation:
    if project is None:
        return PublicAccessEvaluation(
            allowed=False,
            status_code=404,
            code="project_not_found",
            message="Projekt nicht gefunden.",
            workspace=workspace,
            reason="project_missing",
        )

    if _project_is_demo(project):
        return PublicAccessEvaluation(
            allowed=False,
            status_code=403,
            code="demo_project_not_public",
            message="Demo-Projekte sind nicht öffentlich.",
            workspace=workspace,
            reason="demo_project",
        )

    if action not in PUBLIC_READ_ACTIONS:
        return PublicAccessEvaluation(
            allowed=False,
            status_code=403,
            code="public_action_not_allowed",
            message="Diese Aktion ist öffentlich nicht erlaubt.",
            workspace=workspace,
            reason="action_not_public_read",
        )

    if workspace in NEVER_PUBLIC_WORKSPACES:
        return PublicAccessEvaluation(
            allowed=False,
            status_code=403,
            code="workspace_never_public",
            message="Dieser Bereich ist nicht öffentlich verfügbar.",
            workspace=workspace,
            reason="never_public_workspace",
        )

    if workspace not in PUBLIC_WORKSPACES:
        return PublicAccessEvaluation(
            allowed=False,
            status_code=403,
            code="workspace_not_public_supported",
            message="Dieser Workspace ist öffentlich nicht verfügbar.",
            workspace=workspace,
            reason="unsupported_public_workspace",
        )

    publication = _publication_for_project(project)
    visibility = _publication_visibility(project, publication)
    publication_enabled = _publication_enabled(project, publication, visibility)
    workspace_published = _workspace_published(publication, workspace, publication_enabled=publication_enabled)
    require_auth = _publication_require_auth(publication)
    require_permission = _publication_require_project_permission(publication)

    base_kwargs = {
        "visibility": visibility,
        "publication_enabled": publication_enabled,
        "workspace_published": workspace_published,
        "require_auth": require_auth,
        "require_project_permission": require_permission,
        "workspace": workspace,
        "publication": publication,
    }

    if visibility not in {"public", "unlisted"}:
        return PublicAccessEvaluation(
            allowed=False,
            status_code=401,
            code="project_not_public",
            message="Dieses Projekt ist nicht öffentlich freigegeben.",
            reason="visibility_not_public",
            **base_kwargs,
        )

    if not publication_enabled:
        return PublicAccessEvaluation(
            allowed=False,
            status_code=403,
            code="publication_not_enabled",
            message="Die Veröffentlichung ist nicht aktiviert.",
            reason="publication_disabled",
            **base_kwargs,
        )

    if not workspace_published:
        return PublicAccessEvaluation(
            allowed=False,
            status_code=403,
            code="workspace_not_published",
            message="Dieser Workspace ist nicht veröffentlicht.",
            reason="workspace_not_published",
            **base_kwargs,
        )

    if require_auth:
        return PublicAccessEvaluation(
            allowed=False,
            status_code=401,
            code="public_access_requires_auth",
            message="Für diesen öffentlichen Link ist Anmeldung erforderlich.",
            reason="require_auth",
            **base_kwargs,
        )

    if require_permission:
        return PublicAccessEvaluation(
            allowed=False,
            status_code=403,
            code="public_access_requires_project_permission",
            message="Für diesen öffentlichen Link ist eine Projektberechtigung erforderlich.",
            reason="require_project_permission",
            **base_kwargs,
        )

    return PublicAccessEvaluation(
        allowed=True,
        status_code=200,
        code="public_project_allowed",
        message="Öffentlicher Projektzugriff erlaubt.",
        reason="public_workspace_allowed",
        **base_kwargs,
    )


def apply_project_access_context(
    project_payload: Mapping[str, Any],
    access_context: Any,
) -> Dict[str, Any]:
    """
    Merge access context into a serialized project payload.

    This is intentionally data-only and template/API safe.
    """

    try:
        payload = dict(project_payload or {})
        ctx = access_context.to_dict() if hasattr(access_context, "to_dict") else _safe_dict(access_context)

        access = _safe_dict(payload.get("access"))
        permissions = _safe_dict(access.get("permissions"))
        ctx_permissions = _safe_dict(ctx.get("permissions"))

        permissions.update({key: _safe_bool(value, False) for key, value in ctx_permissions.items()})

        access.update(
            {
                "role": ctx.get("role") or access.get("role") or "viewer",
                "source": ctx.get("source") or DEFAULT_SOURCE,
                "access_mode": ctx.get("access_mode") or ctx.get("accessMode") or "",
                "accessMode": ctx.get("access_mode") or ctx.get("accessMode") or "",
                "read_only": _safe_bool(ctx.get("read_only") or ctx.get("readOnly"), True),
                "readOnly": _safe_bool(ctx.get("read_only") or ctx.get("readOnly"), True),
                "public_viewer": _safe_bool(ctx.get("public_viewer") or ctx.get("publicViewer"), False),
                "publicViewer": _safe_bool(ctx.get("public_viewer") or ctx.get("publicViewer"), False),
                "is_public_viewer": _safe_bool(ctx.get("public_viewer") or ctx.get("publicViewer"), False),
                "isPublicViewer": _safe_bool(ctx.get("public_viewer") or ctx.get("publicViewer"), False),
                "demo_mode": _safe_bool(ctx.get("demo_mode") or ctx.get("demoMode"), False),
                "demoMode": _safe_bool(ctx.get("demo_mode") or ctx.get("demoMode"), False),
                "permissions": permissions,
                "can_view": bool(permissions.get("view")),
                "canView": bool(permissions.get("view")),
                "can_edit": bool(permissions.get("edit")),
                "canEdit": bool(permissions.get("edit")),
                "can_manage": bool(permissions.get("manage")),
                "canManage": bool(permissions.get("manage")),
                "can_delete": bool(permissions.get("delete")),
                "canDelete": bool(permissions.get("delete")),
                "can_transfer": bool(permissions.get("transfer")),
                "canTransfer": bool(permissions.get("transfer")),
                "can_embed": bool(permissions.get("embed")),
                "canEmbed": bool(permissions.get("embed")),
                "can_view_settings": bool(permissions.get("view_settings")),
                "canViewSettings": bool(permissions.get("view_settings")),
                "can_manage_settings": bool(permissions.get("manage_settings")),
                "canManageSettings": bool(permissions.get("manage_settings")),
                "can_view_team": bool(permissions.get("view_team")),
                "canViewTeam": bool(permissions.get("view_team")),
                "can_manage_team": bool(permissions.get("manage_team")),
                "canManageTeam": bool(permissions.get("manage_team")),
                "can_view_admin": bool(permissions.get("view_admin")),
                "canViewAdmin": bool(permissions.get("view_admin")),
            }
        )

        payload["access"] = access
        payload["access_mode"] = access.get("access_mode")
        payload["accessMode"] = access.get("accessMode")
        payload["read_only"] = access.get("read_only")
        payload["readOnly"] = access.get("readOnly")
        payload["public_viewer"] = access.get("public_viewer")
        payload["publicViewer"] = access.get("publicViewer")

        if _safe_bool(access.get("public_viewer"), False):
            payload["demo_mode"] = False
            payload["demoMode"] = False

        return payload
    except Exception:
        logger.exception("project_access_context.apply_failed")
        return dict(project_payload or {})


def normalize_workspace(value: Any) -> str:
    try:
        key = _safe_str(value, "project", 80).strip().lower()
        key = key.replace(" ", "_").replace("-", "_")
        return WORKSPACE_ALIASES.get(key, key or "project")
    except Exception:
        return "project"


def normalize_action(value: Any) -> str:
    try:
        key = _safe_str(value, "view", 80).strip().lower()
        key = key.replace(" ", "_").replace("-", "_")
        return ACTION_TO_PERMISSION.get(key, key or "view")
    except Exception:
        return "view"


def clear_project_access_context_cache() -> Dict[str, Any]:
    return {
        "ok": True,
        "cleared": _CACHE.clear(),
        "source": DEFAULT_SOURCE,
    }


def get_project_access_context_cache_status() -> Dict[str, Any]:
    status = _CACHE.status()
    status["source"] = DEFAULT_SOURCE
    return status


def get_project_access_context_status() -> Dict[str, Any]:
    try:
        return {
            "ok": True,
            "source": DEFAULT_SOURCE,
            "cache": get_project_access_context_cache_status(),
            "public_workspaces": sorted(PUBLIC_WORKSPACES),
            "never_public_workspaces": sorted(NEVER_PUBLIC_WORKSPACES),
            "publication_fallback_from_visibility_enabled": _publication_fallback_from_visibility_enabled(),
            "cache_ttl_seconds": _cache_ttl_seconds(),
        }
    except Exception as exc:
        return {
            "ok": False,
            "source": DEFAULT_SOURCE,
            "error": _safe_error(exc),
        }


def _new_project_access_context(
    current_user_context: Mapping[str, Any],
    *,
    workspace: str,
    action: str,
) -> ProjectAccessContext:
    user = _safe_dict(current_user_context)
    auth_unavailable = _is_auth_unavailable_context(user)
    user_blocked = _is_user_blocked_context(user)
    demo_mode = _is_demo_context(user)
    authenticated = _is_authenticated_context(user)
    persistent = _is_persistent_context(user)
    user_id = _current_user_id(user)
    auth_user_id = _auth_user_id(user)

    if auth_unavailable:
        return ProjectAccessContext(
            ok=False,
            allowed=False,
            access_mode="auth_unavailable",
            code=_context_code(user) or "auth_service_unavailable",
            message="vectoplan-auth ist nicht erreichbar.",
            status_code=503,
            workspace=workspace,
            action=action,
            read_only=True,
            authenticated=False,
            persistent=False,
            demo_mode=False,
            user_id=None,
            auth_user_id=auth_user_id,
            project_public_id="new",
            role="none",
            permissions=_permissions_none(),
            reason="auth_unavailable",
        )

    if user_blocked:
        return ProjectAccessContext(
            ok=False,
            allowed=False,
            access_mode="blocked",
            code=_context_code(user) or "auth_blocked",
            message="Dieser Zugang ist gesperrt.",
            status_code=403,
            workspace=workspace,
            action=action,
            read_only=True,
            authenticated=authenticated,
            persistent=False,
            demo_mode=False,
            user_id=user_id,
            auth_user_id=auth_user_id,
            project_public_id="new",
            role="none",
            permissions=_permissions_none(),
            reason="user_blocked",
        )

    if demo_mode:
        return ProjectAccessContext(
            ok=True,
            allowed=True,
            access_mode="demo",
            code="demo_project_shell_allowed",
            message="Demo-Projektverwaltung erlaubt.",
            status_code=200,
            workspace=workspace,
            action=action,
            read_only=False,
            public_viewer=False,
            demo_mode=True,
            authenticated=False,
            persistent=False,
            user_id=None,
            auth_user_id=auth_user_id,
            project_public_id="new",
            role="demo_editor",
            permissions=_permissions_demo(),
            reason="demo_shell_new_project",
            metadata={
                "temporary": True,
                "demo_project_management": True,
            },
        )

    if authenticated and persistent and user_id:
        return ProjectAccessContext(
            ok=True,
            allowed=True,
            access_mode="authenticated",
            code="new_project_allowed",
            message="Neues Projekt erlaubt.",
            status_code=200,
            workspace=workspace,
            action=action,
            read_only=False,
            public_viewer=False,
            demo_mode=False,
            authenticated=True,
            persistent=True,
            user_id=user_id,
            auth_user_id=auth_user_id,
            project_public_id="new",
            role="owner",
            permissions=_permissions_owner(),
            reason="authenticated_new_project",
        )

    return ProjectAccessContext(
        ok=False,
        allowed=False,
        access_mode="anonymous",
        code="login_required",
        message="Für diese Projektaktion ist Anmeldung erforderlich.",
        status_code=401,
        workspace=workspace,
        action=action,
        read_only=True,
        public_viewer=False,
        demo_mode=False,
        authenticated=False,
        persistent=False,
        user_id=None,
        auth_user_id=auth_user_id,
        project_public_id="new",
        role="none",
        permissions=_permissions_none(),
        reason="anonymous_new_project_requires_login_or_demo",
    )


def _publication_for_project(project: Any) -> Dict[str, Any]:
    try:
        if project is None:
            return {}

        cache_key = "publication:" + _project_public_id(project, "") + ":" + str(_project_updated_marker(project))
        cached = _CACHE.get(cache_key)
        if isinstance(cached, dict):
            return cached

        publication: Dict[str, Any] = {}

        if get_project_publication is not None:
            try:
                result = get_project_publication(
                    project,
                    actor_user_id=None,
                    include_private=False,
                    for_public=True,
                    use_cache=False,
                )
                result_dict = _safe_dict(result)
                publication = _safe_dict(result_dict.get("publication") or result_dict)
                nested_publication = _safe_dict(publication.get("publication"))
                if nested_publication:
                    publication = nested_publication
            except TypeError:
                try:
                    result = get_project_publication(project)
                    result_dict = _safe_dict(result)
                    publication = _safe_dict(result_dict.get("publication") or result_dict)
                    nested_publication = _safe_dict(publication.get("publication"))
                    if nested_publication:
                        publication = nested_publication
                except Exception:
                    logger.debug("project_access_context.get_publication_legacy_failed", exc_info=True)
            except Exception:
                logger.debug("project_access_context.get_publication_failed", exc_info=True)

        if not publication:
            publication = _publication_from_project_fields(project)

        _CACHE.set(cache_key, publication, _cache_ttl_seconds())
        return publication
    except Exception:
        logger.exception("project_access_context.publication_for_project_failed")
        return _publication_from_project_fields(project)


def _publication_from_project_fields(project: Any) -> Dict[str, Any]:
    try:
        visibility = _safe_str(_safe_get(project, "visibility"), "private", 80).lower()
        is_public = _safe_bool(_safe_get(project, "is_public"), False) or visibility in {"public", "unlisted"}

        settings = _safe_dict(_safe_get(project, "settings"))
        metadata = _safe_dict(_safe_get(project, "metadata_json"))
        publication = _safe_dict(settings.get("publication") or metadata.get("publication"))

        if publication:
            publication.setdefault("visibility", visibility)
            return publication

        published_workspaces = {}
        if _publication_fallback_from_visibility_enabled() and is_public:
            published_workspaces = {
                "project": True,
                "map": False,
                "editor3d": False,
                "cad2d": False,
                "lv": False,
                "files": False,
                "structural_calculation": False,
                "energy_calculation": False,
                "sound_protection_calculation": False,
            }

        return {
            "visibility": visibility,
            "publication_enabled": bool(is_public and _publication_fallback_from_visibility_enabled()),
            "published": bool(is_public and _publication_fallback_from_visibility_enabled()),
            "public": bool(is_public and _publication_fallback_from_visibility_enabled()),
            "is_public": bool(is_public),
            "published_workspaces": published_workspaces,
            "effective_published_workspaces": published_workspaces,
            "require_auth": True if not _publication_fallback_from_visibility_enabled() else False,
            "require_project_permission": True if not _publication_fallback_from_visibility_enabled() else False,
            "source": "project_fields_fallback",
        }
    except Exception:
        return {
            "visibility": "private",
            "publication_enabled": False,
            "published_workspaces": {},
            "effective_published_workspaces": {},
            "require_auth": True,
            "require_project_permission": True,
            "source": "project_fields_fallback_failed",
        }


def _publication_visibility(project: Any, publication: Mapping[str, Any]) -> str:
    visibility = _safe_str(
        publication.get("visibility")
        or publication.get("project_visibility")
        or publication.get("projectVisibility")
        or _safe_get(project, "visibility"),
        "private",
        80,
    ).lower()

    if visibility not in {"private", "unlisted", "public"}:
        if _safe_bool(publication.get("is_public") or publication.get("public") or _safe_get(project, "is_public"), False):
            return "public"
        return "private"

    return visibility


def _publication_enabled(project: Any, publication: Mapping[str, Any], visibility: str) -> bool:
    explicit = (
        publication.get("publication_enabled")
        if "publication_enabled" in publication
        else publication.get("publicationEnabled")
        if "publicationEnabled" in publication
        else publication.get("published")
        if "published" in publication
        else publication.get("public")
        if "public" in publication
        else None
    )

    if explicit is not None:
        return _safe_bool(explicit, False)

    if _publication_fallback_from_visibility_enabled():
        return visibility in {"public", "unlisted"} or _safe_bool(_safe_get(project, "is_public"), False)

    return False


def _workspace_published(
    publication: Mapping[str, Any],
    workspace: str,
    *,
    publication_enabled: bool,
) -> bool:
    if not publication_enabled:
        return False

    effective = _safe_dict(
        publication.get("effective_published_workspaces")
        or publication.get("effectivePublishedWorkspaces")
    )
    published = _safe_dict(
        publication.get("published_workspaces")
        or publication.get("publishedWorkspaces")
    )

    combined: Dict[str, Any] = {}
    combined.update(published)
    combined.update(effective)

    if not combined and _publication_fallback_from_visibility_enabled():
        return workspace == "project"

    value = (
        combined.get(workspace)
        if workspace in combined
        else combined.get(_workspace_legacy_key(workspace))
    )

    return _safe_bool(value, False)


def _publication_require_auth(publication: Mapping[str, Any]) -> bool:
    if "require_auth" in publication:
        return _safe_bool(publication.get("require_auth"), True)
    if "requireAuth" in publication:
        return _safe_bool(publication.get("requireAuth"), True)
    if "auth_required" in publication:
        return _safe_bool(publication.get("auth_required"), True)
    return True


def _publication_require_project_permission(publication: Mapping[str, Any]) -> bool:
    if "require_project_permission" in publication:
        return _safe_bool(publication.get("require_project_permission"), True)
    if "requireProjectPermission" in publication:
        return _safe_bool(publication.get("requireProjectPermission"), True)
    if "permission_required" in publication:
        return _safe_bool(publication.get("permission_required"), True)
    return True


def _workspace_legacy_key(workspace: str) -> str:
    if workspace == "editor3d":
        return "3d"
    if workspace == "cad2d":
        return "2d"
    return workspace


def _anonymous_denial_code(public_eval: PublicAccessEvaluation) -> str:
    if public_eval.code in {"public_access_requires_auth", "project_not_public"}:
        return "login_required"
    return public_eval.code or "project_permission_denied"


def _anonymous_denial_status(public_eval: PublicAccessEvaluation) -> int:
    if public_eval.code in {"public_access_requires_auth", "project_not_public"}:
        return 401
    return public_eval.status_code or 403


def _anonymous_denial_message(public_eval: PublicAccessEvaluation) -> str:
    if public_eval.code == "project_not_public":
        return "Für dieses Projekt ist Anmeldung erforderlich."
    return public_eval.message or "Zugriff verweigert."


def _permissions_none() -> Dict[str, bool]:
    return {
        "view": False,
        "edit": False,
        "manage": False,
        "delete": False,
        "transfer": False,
        "embed": False,
        "view_settings": False,
        "manage_settings": False,
        "view_team": False,
        "manage_team": False,
        "view_admin": False,
    }


def _permissions_view_only() -> Dict[str, bool]:
    permissions = _permissions_none()
    permissions.update(
        {
            "view": True,
            "embed": True,
        }
    )
    return permissions


def _permissions_public() -> Dict[str, bool]:
    return _permissions_view_only()


def _permissions_demo() -> Dict[str, bool]:
    permissions = _permissions_none()
    permissions.update(
        {
            "view": True,
            "edit": True,
            "manage": False,
            "delete": False,
            "transfer": False,
            "embed": True,
            "view_settings": False,
            "manage_settings": False,
            "view_team": False,
            "manage_team": False,
            "view_admin": False,
        }
    )
    return permissions


def _permissions_owner() -> Dict[str, bool]:
    return {
        "view": True,
        "edit": True,
        "manage": True,
        "delete": True,
        "transfer": True,
        "embed": True,
        "view_settings": True,
        "manage_settings": True,
        "view_team": True,
        "manage_team": True,
        "view_admin": True,
    }


def _permissions_from_payload(payload: Mapping[str, Any]) -> Dict[str, bool]:
    try:
        source = _safe_dict(payload.get("permissions"))
        if not source:
            source = {
                "view": payload.get("can_view") or payload.get("canView"),
                "edit": payload.get("can_edit") or payload.get("canEdit"),
                "manage": payload.get("can_manage") or payload.get("canManage"),
                "delete": payload.get("can_delete") or payload.get("canDelete"),
                "transfer": payload.get("can_transfer") or payload.get("canTransfer"),
                "embed": payload.get("can_embed") or payload.get("canEmbed"),
                "view_settings": payload.get("can_view_settings") or payload.get("canViewSettings"),
                "manage_settings": payload.get("can_manage_settings") or payload.get("canManageSettings"),
                "view_team": payload.get("can_view_team") or payload.get("canViewTeam"),
                "manage_team": payload.get("can_manage_team") or payload.get("canManageTeam"),
                "view_admin": payload.get("can_view_admin") or payload.get("canViewAdmin"),
            }

        result = _permissions_none()
        for key in result.keys():
            result[key] = _safe_bool(source.get(key), False)

        if result["manage"]:
            result["view"] = True
            result["edit"] = True
            result["embed"] = True
            result["view_settings"] = True
            result["view_team"] = True
            result["view_admin"] = True

        if result["edit"]:
            result["view"] = True

        return result
    except Exception:
        return _permissions_none()


def _safe_get(source: Any, key: str, default: Any = None) -> Any:
    try:
        if source is None or not key:
            return default

        if isinstance(source, Mapping):
            if key in source:
                return source.get(key, default)
            return default

        if hasattr(source, key):
            value = getattr(source, key)
            if callable(value):
                return default
            return value

        return default
    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "to_dict") and callable(value.to_dict):
            data = value.to_dict()
            return dict(data) if isinstance(data, Mapping) else {}
        return {}
    except Exception:
        return {}


def _safe_str(value: Any, default: str = "", max_len: int = 240) -> str:
    try:
        text = str(value if value is not None else default).strip()
        if not text:
            text = default
        if max_len > 0 and len(text) > max_len:
            return text[:max_len]
        return text
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        text = str(value).strip()
        if not text:
            return default
        return int(text)
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        if value is None:
            return default

        text = _safe_str(value, "", 80).lower()

        if text in TRUTHY_VALUES:
            return True

        if text in FALSY_VALUES:
            return False

        return default
    except Exception:
        return default


def _project_id(project: Any) -> Optional[Any]:
    try:
        if project is None:
            return None
        return _safe_get(project, "id")
    except Exception:
        return None


def _project_public_id(project: Any, default: str = "") -> str:
    try:
        if project is None:
            return default
        return (
            _safe_str(_safe_get(project, "public_id"), "", 160)
            or _safe_str(_safe_get(project, "publicId"), "", 160)
            or _safe_str(_safe_get(project, "project_public_id"), "", 160)
            or _safe_str(_safe_get(project, "projectPublicId"), "", 160)
            or _safe_str(_safe_get(project, "id"), default, 160)
        )
    except Exception:
        return default


def _project_is_demo(project: Any) -> bool:
    try:
        if project is None:
            return False

        if _safe_bool(_safe_get(project, "is_demo"), False):
            return True

        if _safe_bool(_safe_get(project, "demo_mode"), False):
            return True

        if _safe_str(_safe_get(project, "project_scope"), "", 80).lower() == "demo":
            return True

        metadata = _safe_dict(_safe_get(project, "metadata_json"))
        demo_meta = _safe_dict(metadata.get("vectoplan_demo"))
        return _safe_bool(demo_meta.get("enabled"), False)
    except Exception:
        return False


def _project_updated_marker(project: Any) -> str:
    try:
        for key in ("updated_at", "updatedAt", "modified_at", "modifiedAt", "version", "revision"):
            value = _safe_get(project, key)
            if value:
                return _safe_str(value, "", 120)
        return ""
    except Exception:
        return ""


def _current_user_id(current_user: Mapping[str, Any]) -> Optional[int]:
    value = (
        current_user.get("user_id")
        or current_user.get("userId")
        or current_user.get("id")
        or current_user.get("app_user_id")
        or current_user.get("appUserId")
    )
    parsed = _safe_int(value, 0)
    return parsed if parsed > 0 else None


def _auth_user_id(current_user: Mapping[str, Any]) -> str:
    return _safe_str(
        current_user.get("auth_user_id")
        or current_user.get("authUserId")
        or current_user.get("sub")
        or current_user.get("subject")
        or current_user.get("external_user_id")
        or current_user.get("externalUserId"),
        "",
        160,
    )


def _is_authenticated_context(current_user: Mapping[str, Any]) -> bool:
    if _is_auth_unavailable_context(current_user) or _is_user_blocked_context(current_user):
        return False

    return _safe_bool(
        current_user.get("authenticated")
        or current_user.get("is_authenticated")
        or current_user.get("isAuthenticated"),
        False,
    )


def _is_persistent_context(current_user: Mapping[str, Any]) -> bool:
    if _is_auth_unavailable_context(current_user) or _is_user_blocked_context(current_user):
        return False

    if _is_demo_context(current_user):
        return False

    return bool(_safe_bool(current_user.get("persistent"), False) and _current_user_id(current_user))


def _is_demo_context(current_user: Mapping[str, Any]) -> bool:
    if _is_auth_unavailable_context(current_user) or _is_user_blocked_context(current_user):
        return False

    demo_mode = _safe_bool(
        current_user.get("demo_mode")
        or current_user.get("demoMode")
        or current_user.get("is_demo")
        or current_user.get("isDemo"),
        False,
    )
    can_demo = _safe_bool(current_user.get("can_demo") or current_user.get("canDemo"), True)

    return bool(demo_mode and can_demo)


def _is_auth_unavailable_context(current_user: Mapping[str, Any]) -> bool:
    code = _context_code(current_user)
    blocked_kind = _safe_str(current_user.get("blocked_kind") or current_user.get("blockedKind"), "", 80).lower()
    status = _safe_int(
        current_user.get("denial_status_code")
        or current_user.get("denialStatusCode")
        or current_user.get("status_code"),
        0,
    )

    return bool(
        _safe_bool(current_user.get("auth_unavailable") or current_user.get("authUnavailable"), False)
        or blocked_kind == "auth_unavailable"
        or code in {
            "auth_unavailable",
            "auth_service_unavailable",
            "service_unavailable",
            "dependency_unavailable",
            "upstream_unavailable",
            "storage_unavailable",
            "dns_failed",
            "connection_refused",
            "timeout",
            "http_5xx",
            "http_error",
            "access_denied",
            "invalid_payload",
            "not_configured",
            "request_failed",
            "requests_unavailable",
            "current_user_unavailable",
        }
        or status == 503
    )


def _is_user_blocked_context(current_user: Mapping[str, Any]) -> bool:
    if _is_auth_unavailable_context(current_user):
        return False

    code = _context_code(current_user)
    blocked_kind = _safe_str(current_user.get("blocked_kind") or current_user.get("blockedKind"), "", 80).lower()

    return bool(
        _safe_bool(current_user.get("user_blocked") or current_user.get("userBlocked"), False)
        or blocked_kind == "user_blocked"
        or code in {
            "blocked",
            "banned",
            "user_blocked",
            "user_banned",
            "account_blocked",
            "account_banned",
            "subscription_blocked",
            "plan_blocked",
            "security_blocked",
            "disabled",
            "inactive",
            "suspended",
            "deleted",
            "locked",
        }
    )


def _context_code(current_user: Mapping[str, Any]) -> str:
    return _safe_str(
        current_user.get("blocked_reason")
        or current_user.get("blockedReason")
        or current_user.get("reason_code")
        or current_user.get("reasonCode")
        or current_user.get("auth_state")
        or current_user.get("authState")
        or current_user.get("code"),
        "",
        160,
    ).lower()


def _result_cacheable(result: ProjectAccessContext) -> bool:
    if not result:
        return False
    if result.status_code >= 500:
        return False
    return True


def _cache_key(
    *,
    project: Any,
    current_user_context: Mapping[str, Any],
    workspace: str,
    action: str,
) -> str:
    user_id = _current_user_id(current_user_context)
    auth_user_id = _auth_user_id(current_user_context)
    identity = str(user_id or auth_user_id or "anonymous")
    demo = "demo" if _is_demo_context(current_user_context) else "normal"

    return ":".join(
        [
            "project_access",
            _project_public_id(project, "none"),
            str(_project_updated_marker(project)),
            identity,
            demo,
            workspace,
            action,
        ]
    )


def _cache_ttl_seconds() -> int:
    try:
        raw = _config_value("PROJECT_ACCESS_CONTEXT_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS)
        ttl = int(raw)
        if ttl < 0:
            return DEFAULT_CACHE_TTL_SECONDS
        return min(ttl, 300)
    except Exception:
        return DEFAULT_CACHE_TTL_SECONDS


def _publication_fallback_from_visibility_enabled() -> bool:
    return _config_bool("PROJECT_ACCESS_PUBLIC_FALLBACK_FROM_VISIBILITY", False)


def _config_bool(key: str, default: bool) -> bool:
    return _safe_bool(_config_value(key, default), default)


def _config_value(key: str, default: Any = None) -> Any:
    try:
        if has_app_context() and current_app is not None:
            value = current_app.config.get(key)
            if value is not None:
                return value

        value = os.environ.get(key)
        if value is not None:
            return value

        return default
    except Exception:
        return default


def _sanitize_dict(value: Mapping[str, Any], *, max_items: int = 80) -> Dict[str, Any]:
    try:
        result: Dict[str, Any] = {}
        for index, (key, raw_value) in enumerate(value.items()):
            if index >= max_items:
                break

            safe_key = _safe_str(key, "", 120)
            if not safe_key:
                continue

            lower_key = safe_key.lower()
            if any(part in lower_key for part in ("token", "secret", "password", "cookie", "authorization", "api_key", "apikey")):
                result[safe_key] = "[redacted]"
            elif isinstance(raw_value, Mapping):
                result[safe_key] = _sanitize_dict(raw_value, max_items=30)
            elif isinstance(raw_value, (list, tuple, set)):
                result[safe_key] = [_safe_str(item, "", 300) for item in list(raw_value)[:20]]
            elif isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
                result[safe_key] = raw_value
            else:
                result[safe_key] = _safe_str(raw_value, "", 300)

        return result
    except Exception:
        return {}


def _sanitized_permission_payload(value: Mapping[str, Any]) -> Dict[str, Any]:
    allowed_keys = {
        "role",
        "source",
        "permissions",
        "can_view",
        "can_edit",
        "can_manage",
        "can_delete",
        "can_transfer",
        "can_embed",
        "can_view_settings",
        "can_manage_settings",
        "can_view_team",
        "can_manage_team",
        "can_view_admin",
    }
    return {key: copy.deepcopy(value.get(key)) for key in allowed_keys if key in value}


def _safe_error(exc: BaseException) -> str:
    try:
        return f"{exc.__class__.__name__}: {_safe_str(str(exc), 'error', 300)}"
    except Exception:
        return "error"


__all__ = [
    "ProjectAccessContext",
    "PublicAccessEvaluation",
    "resolve_project_access",
    "resolve_project_shell_access",
    "resolve_project_workspace_access",
    "evaluate_public_project_access",
    "apply_project_access_context",
    "normalize_workspace",
    "normalize_action",
    "clear_project_access_context_cache",
    "get_project_access_context_cache_status",
    "get_project_access_context_status",
]
