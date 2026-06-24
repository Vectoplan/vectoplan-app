# services/vectoplan-app/models/project_embed.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from .base import (
    SerializationMixin,
    TimestampMixin,
    db,
    isoformat,
    json_type,
    safe_bool,
    safe_dict,
    safe_int,
    safe_list,
    safe_slug,
    safe_str,
    utcnow,
)


EMBED_MODE_SPECTATOR = "spectator"
EMBED_MODE_READONLY = "readonly"
EMBED_MODE_INTERACTIVE = "interactive"
EMBED_MODE_EDITOR = "editor"
EMBED_MODE_ADMIN = "admin"

EMBED_MODES = {
    EMBED_MODE_SPECTATOR,
    EMBED_MODE_READONLY,
    EMBED_MODE_INTERACTIVE,
    EMBED_MODE_EDITOR,
    EMBED_MODE_ADMIN,
}

DEFAULT_EMBED_MODE = EMBED_MODE_SPECTATOR
DEFAULT_ALLOWED_MODES = [EMBED_MODE_SPECTATOR, EMBED_MODE_READONLY]


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

    While models/core.py still defines ProjectEmbedPolicy, this module returns
    the already registered core model instead of defining a duplicate table.
    After core.py becomes an aggregator, this module owns the model.
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
# Normalization helpers
# ─────────────────────────────────────────────────────────────

def normalize_embed_mode(value: Any, default: str = DEFAULT_EMBED_MODE) -> str:
    try:
        text = safe_slug(value, default=default, max_len=40).replace("-", "_")

        aliases = {
            "view": EMBED_MODE_SPECTATOR,
            "viewer": EMBED_MODE_SPECTATOR,
            "spectate": EMBED_MODE_SPECTATOR,
            "spectator_only": EMBED_MODE_SPECTATOR,
            "read": EMBED_MODE_READONLY,
            "read_only": EMBED_MODE_READONLY,
            "readonly": EMBED_MODE_READONLY,
            "ro": EMBED_MODE_READONLY,
            "interact": EMBED_MODE_INTERACTIVE,
            "interactive": EMBED_MODE_INTERACTIVE,
            "edit": EMBED_MODE_EDITOR,
            "editor": EMBED_MODE_EDITOR,
            "write": EMBED_MODE_EDITOR,
            "manage": EMBED_MODE_ADMIN,
            "admin": EMBED_MODE_ADMIN,
        }

        normalized = aliases.get(text, text)

        if normalized not in EMBED_MODES:
            return default

        return normalized

    except Exception:
        return default


def normalize_embed_modes(values: Any, default: Optional[Sequence[str]] = None) -> List[str]:
    try:
        fallback = list(default or DEFAULT_ALLOWED_MODES)
        raw_items = safe_list(values, fallback)

        result: List[str] = []
        for item in raw_items:
            mode = normalize_embed_mode(item, "")
            if mode and mode not in result:
                result.append(mode)

        return result or fallback

    except Exception:
        return list(default or DEFAULT_ALLOWED_MODES)


def normalize_origin(value: Any) -> str:
    try:
        text = safe_str(value, "", 500)

        if not text:
            return ""

        if text in {"self", "'self'"}:
            return "'self'"

        if text == "*":
            return "*"

        parsed = urlsplit(text)

        if parsed.scheme not in {"http", "https"}:
            return ""

        if not parsed.netloc:
            return ""

        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    except Exception:
        return ""


def normalize_origins(values: Any) -> List[str]:
    try:
        raw_items = safe_list(values, [])

        result: List[str] = []
        for item in raw_items:
            origin = normalize_origin(item)
            if origin and origin not in result:
                result.append(origin)

        return result

    except Exception:
        return []


def mode_is_readonly(mode: Any) -> bool:
    try:
        normalized = normalize_embed_mode(mode)

        return normalized in {
            EMBED_MODE_SPECTATOR,
            EMBED_MODE_READONLY,
        }

    except Exception:
        return True


def mode_allows_interaction(mode: Any) -> bool:
    try:
        normalized = normalize_embed_mode(mode)

        return normalized in {
            EMBED_MODE_INTERACTIVE,
            EMBED_MODE_EDITOR,
            EMBED_MODE_ADMIN,
        }

    except Exception:
        return False


def _datetime_expired(value: Any) -> bool:
    try:
        if value is None:
            return False

        if isinstance(value, datetime):
            return value <= utcnow()

        return False

    except Exception:
        return False


def _origin_allowed(origin: Any, allowed: Sequence[str], denied: Sequence[str]) -> bool:
    try:
        normalized = normalize_origin(origin)

        if not normalized:
            return False

        denied_set = set(normalize_origins(denied))
        if normalized in denied_set:
            return False

        allowed_list = normalize_origins(allowed)

        if not allowed_list:
            return True

        if "*" in allowed_list:
            return True

        if normalized in allowed_list:
            return True

        return False

    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
# ProjectEmbedPolicy model
# ─────────────────────────────────────────────────────────────

def _define_project_embed_policy_model(*, extend_existing: bool = False):
    class ProjectEmbedPolicy(TimestampMixin, SerializationMixin, db.Model):
        """
        App-owned embed/iframe policy for a project.

        This table controls how a project may be embedded from the app shell.
        It stores policy and references only. It does not store editor, map,
        chunk, 2D or LV content.

        Important:
        - Browser/public URL decisions still belong to the route layer.
        - Internal Docker URLs must never be exposed through this model.
        """

        __tablename__ = "project_embed_policies"
        __table_args__ = _table_args(
            extend_existing,
            db.UniqueConstraint("project_id", name="uq_project_embed_policies_project_id"),
        )

        id = db.Column(db.Integer, primary_key=True)

        project_id = db.Column(db.Integer, nullable=False, index=True)

        enabled = db.Column(db.Boolean, nullable=False, default=True, index=True)
        allow_iframe = db.Column(db.Boolean, nullable=False, default=True, index=True)
        allow_public_embed = db.Column(db.Boolean, nullable=False, default=False, index=True)

        mode = db.Column(db.String(40), nullable=False, default=DEFAULT_EMBED_MODE, index=True)
        default_mode = db.Column(db.String(40), nullable=False, default=DEFAULT_EMBED_MODE, index=True)
        allowed_modes = db.Column(json_type(), nullable=False, default=lambda: list(DEFAULT_ALLOWED_MODES))

        spectator_only = db.Column(db.Boolean, nullable=False, default=True, index=True)
        readonly = db.Column(db.Boolean, nullable=False, default=True, index=True)
        allow_interaction = db.Column(db.Boolean, nullable=False, default=False, index=True)

        allow_map = db.Column(db.Boolean, nullable=False, default=True)
        allow_editor3d = db.Column(db.Boolean, nullable=False, default=True)
        allow_2d = db.Column(db.Boolean, nullable=False, default=True)
        allow_lv = db.Column(db.Boolean, nullable=False, default=True)
        allow_versions = db.Column(db.Boolean, nullable=False, default=False)
        allow_downloads = db.Column(db.Boolean, nullable=False, default=False)

        require_auth = db.Column(db.Boolean, nullable=False, default=True, index=True)
        require_project_permission = db.Column(db.Boolean, nullable=False, default=True, index=True)
        require_token = db.Column(db.Boolean, nullable=False, default=False, index=True)

        token_hash = db.Column(db.String(255), nullable=True, index=True)
        token_label = db.Column(db.String(255), nullable=True)
        token_expires_at = db.Column(db.DateTime, nullable=True, index=True)

        allowed_origins = db.Column(json_type(), nullable=False, default=list)
        denied_origins = db.Column(json_type(), nullable=False, default=list)

        max_session_seconds = db.Column(db.Integer, nullable=True)
        max_width = db.Column(db.Integer, nullable=True)
        max_height = db.Column(db.Integer, nullable=True)

        status = db.Column(db.String(40), nullable=False, default="active", index=True)

        created_by_user_id = db.Column(db.Integer, nullable=True, index=True)
        updated_by_user_id = db.Column(db.Integer, nullable=True, index=True)

        settings = db.Column(json_type(), nullable=False, default=dict)
        metadata_json = db.Column("metadata", json_type(), nullable=False, default=dict)

        def __repr__(self) -> str:
            try:
                return (
                    f"<ProjectEmbedPolicy project_id={self.project_id!r} "
                    f"enabled={self.enabled!r} mode={self.mode!r}>"
                )
            except Exception:
                return "<ProjectEmbedPolicy>"

        @property
        def is_enabled(self) -> bool:
            try:
                if not bool(self.enabled):
                    return False

                if str(self.status or "").lower() not in {"active", "enabled"}:
                    return False

                if self.require_token and _datetime_expired(self.token_expires_at):
                    return False

                return True

            except Exception:
                return False

        @property
        def is_public(self) -> bool:
            try:
                return bool(self.allow_public_embed)
            except Exception:
                return False

        @property
        def effective_mode(self) -> str:
            try:
                mode = normalize_embed_mode(self.mode, DEFAULT_EMBED_MODE)

                if self.spectator_only:
                    return EMBED_MODE_SPECTATOR

                allowed = normalize_embed_modes(self.allowed_modes)

                if mode in allowed:
                    return mode

                return normalize_embed_mode(self.default_mode, DEFAULT_EMBED_MODE)

            except Exception:
                return DEFAULT_EMBED_MODE

        @property
        def effective_readonly(self) -> bool:
            try:
                if self.spectator_only:
                    return True

                if self.readonly:
                    return True

                return mode_is_readonly(self.effective_mode)

            except Exception:
                return True

        @property
        def effective_interactive(self) -> bool:
            try:
                if self.effective_readonly:
                    return False

                return bool(self.allow_interaction) and mode_allows_interaction(self.effective_mode)

            except Exception:
                return False

        def normalize(self) -> "ProjectEmbedPolicy":
            try:
                self.project_id = safe_int(self.project_id, 0, minimum=1)

                self.enabled = safe_bool(self.enabled, True)
                self.allow_iframe = safe_bool(self.allow_iframe, True)
                self.allow_public_embed = safe_bool(self.allow_public_embed, False)

                self.mode = normalize_embed_mode(self.mode, DEFAULT_EMBED_MODE)
                self.default_mode = normalize_embed_mode(self.default_mode, DEFAULT_EMBED_MODE)
                self.allowed_modes = normalize_embed_modes(self.allowed_modes, DEFAULT_ALLOWED_MODES)

                if self.default_mode not in self.allowed_modes:
                    self.allowed_modes = [self.default_mode, *self.allowed_modes]

                self.spectator_only = safe_bool(self.spectator_only, self.mode == EMBED_MODE_SPECTATOR)
                self.readonly = safe_bool(self.readonly, mode_is_readonly(self.mode))
                self.allow_interaction = safe_bool(self.allow_interaction, mode_allows_interaction(self.mode))

                if self.spectator_only:
                    self.mode = EMBED_MODE_SPECTATOR
                    self.default_mode = EMBED_MODE_SPECTATOR
                    self.readonly = True
                    self.allow_interaction = False

                self.allow_map = safe_bool(self.allow_map, True)
                self.allow_editor3d = safe_bool(self.allow_editor3d, True)
                self.allow_2d = safe_bool(self.allow_2d, True)
                self.allow_lv = safe_bool(self.allow_lv, True)
                self.allow_versions = safe_bool(self.allow_versions, False)
                self.allow_downloads = safe_bool(self.allow_downloads, False)

                self.require_auth = safe_bool(self.require_auth, True)
                self.require_project_permission = safe_bool(self.require_project_permission, True)
                self.require_token = safe_bool(self.require_token, False)

                self.token_hash = safe_str(self.token_hash, "", 255) or None
                self.token_label = safe_str(self.token_label, "", 255) or None

                self.allowed_origins = normalize_origins(self.allowed_origins)
                self.denied_origins = normalize_origins(self.denied_origins)

                self.max_session_seconds = safe_int(self.max_session_seconds, 0, minimum=0) or None
                self.max_width = safe_int(self.max_width, 0, minimum=0) or None
                self.max_height = safe_int(self.max_height, 0, minimum=0) or None

                self.status = safe_str(self.status, "active", 40) or "active"
                self.created_by_user_id = safe_int(self.created_by_user_id, 0) or None
                self.updated_by_user_id = safe_int(self.updated_by_user_id, 0) or None

                self.settings = safe_dict(self.settings)
                self.metadata_json = safe_dict(self.metadata_json)

                return self

            except Exception:
                return self

        def apply_mode(self, mode: Any, *, spectator_only: Optional[bool] = None) -> "ProjectEmbedPolicy":
            try:
                normalized = normalize_embed_mode(mode, DEFAULT_EMBED_MODE)

                self.mode = normalized
                self.default_mode = normalized

                if normalized not in normalize_embed_modes(self.allowed_modes):
                    self.allowed_modes = [normalized, *normalize_embed_modes(self.allowed_modes)]

                if spectator_only is not None:
                    self.spectator_only = bool(spectator_only)
                else:
                    self.spectator_only = normalized == EMBED_MODE_SPECTATOR

                self.readonly = mode_is_readonly(normalized)
                self.allow_interaction = mode_allows_interaction(normalized) and not self.readonly

                self.normalize()
                self.touch()

                return self

            except Exception:
                return self

        def update_from_payload(self, payload: Optional[Mapping[str, Any]] = None) -> "ProjectEmbedPolicy":
            try:
                data = safe_dict(payload)

                field_map = {
                    "enabled": "enabled",
                    "embed_enabled": "enabled",
                    "allow_iframe": "allow_iframe",
                    "allowIframe": "allow_iframe",
                    "allow_public_embed": "allow_public_embed",
                    "allowPublicEmbed": "allow_public_embed",
                    "public": "allow_public_embed",
                    "mode": "mode",
                    "default_mode": "default_mode",
                    "defaultMode": "default_mode",
                    "spectator_only": "spectator_only",
                    "spectatorOnly": "spectator_only",
                    "readonly": "readonly",
                    "read_only": "readonly",
                    "allow_interaction": "allow_interaction",
                    "allowInteraction": "allow_interaction",
                    "allow_map": "allow_map",
                    "allowMap": "allow_map",
                    "allow_editor3d": "allow_editor3d",
                    "allowEditor3d": "allow_editor3d",
                    "allow_2d": "allow_2d",
                    "allow2d": "allow_2d",
                    "allow_lv": "allow_lv",
                    "allowLv": "allow_lv",
                    "allow_versions": "allow_versions",
                    "allowVersions": "allow_versions",
                    "allow_downloads": "allow_downloads",
                    "allowDownloads": "allow_downloads",
                    "require_auth": "require_auth",
                    "requireAuth": "require_auth",
                    "require_project_permission": "require_project_permission",
                    "requireProjectPermission": "require_project_permission",
                    "require_token": "require_token",
                    "requireToken": "require_token",
                    "token_hash": "token_hash",
                    "tokenHash": "token_hash",
                    "token_label": "token_label",
                    "tokenLabel": "token_label",
                    "token_expires_at": "token_expires_at",
                    "tokenExpiresAt": "token_expires_at",
                    "max_session_seconds": "max_session_seconds",
                    "maxSessionSeconds": "max_session_seconds",
                    "max_width": "max_width",
                    "maxWidth": "max_width",
                    "max_height": "max_height",
                    "maxHeight": "max_height",
                    "status": "status",
                }

                for source_key, target_key in field_map.items():
                    if source_key in data:
                        setattr(self, target_key, data.get(source_key))

                if "allowed_modes" in data or "allowedModes" in data:
                    self.allowed_modes = normalize_embed_modes(
                        data.get("allowed_modes") or data.get("allowedModes"),
                        DEFAULT_ALLOWED_MODES,
                    )

                if "allowed_origins" in data or "allowedOrigins" in data:
                    self.allowed_origins = normalize_origins(
                        data.get("allowed_origins") or data.get("allowedOrigins")
                    )

                if "denied_origins" in data or "deniedOrigins" in data:
                    self.denied_origins = normalize_origins(
                        data.get("denied_origins") or data.get("deniedOrigins")
                    )

                if "settings" in data:
                    self.settings = safe_dict(data.get("settings"))

                if "metadata" in data or "meta" in data:
                    self.metadata_json = safe_dict(data.get("metadata") or data.get("meta"))

                if "updated_by_user_id" in data or "updatedByUserId" in data:
                    self.updated_by_user_id = safe_int(
                        data.get("updated_by_user_id") or data.get("updatedByUserId"),
                        0,
                    ) or None

                self.normalize()
                self.touch()

                return self

            except Exception:
                return self

        def origin_allowed(self, origin: Any) -> bool:
            try:
                if not self.is_enabled:
                    return False

                return _origin_allowed(origin, self.allowed_origins, self.denied_origins)

            except Exception:
                return False

        def mode_allowed(self, mode: Any) -> bool:
            try:
                normalized = normalize_embed_mode(mode, "")

                if not normalized:
                    return False

                return normalized in normalize_embed_modes(self.allowed_modes)

            except Exception:
                return False

        def feature_allowed(self, feature: Any) -> bool:
            try:
                key = safe_slug(feature, "", 80)

                mapping = {
                    "map": "allow_map",
                    "openlayer": "allow_map",
                    "editor": "allow_editor3d",
                    "editor3d": "allow_editor3d",
                    "3d": "allow_editor3d",
                    "cad": "allow_2d",
                    "cad2d": "allow_2d",
                    "2d": "allow_2d",
                    "lv": "allow_lv",
                    "leistungsverzeichnis": "allow_lv",
                    "versions": "allow_versions",
                    "version": "allow_versions",
                    "downloads": "allow_downloads",
                    "download": "allow_downloads",
                }

                field = mapping.get(key)

                if not field:
                    return False

                return safe_bool(getattr(self, field, False), False)

            except Exception:
                return False

        def disable(self) -> None:
            try:
                self.enabled = False
                self.status = "disabled"
                self.touch()
            except Exception:
                pass

        def enable(self) -> None:
            try:
                self.enabled = True
                self.status = "active"
                self.touch()
            except Exception:
                pass

        def set_token(
            self,
            *,
            token_hash: Any,
            label: str = "",
            expires_at: Optional[datetime] = None,
            require_token: bool = True,
        ) -> None:
            try:
                self.token_hash = safe_str(token_hash, "", 255) or None
                self.token_label = safe_str(label, "", 255) or None
                self.token_expires_at = expires_at
                self.require_token = bool(require_token)
                self.touch()
            except Exception:
                pass

        def clear_token(self) -> None:
            try:
                self.token_hash = None
                self.token_label = None
                self.token_expires_at = None
                self.require_token = False
                self.touch()
            except Exception:
                pass

        def to_dict(self, *, include_private: bool = False) -> Dict[str, Any]:
            try:
                payload: Dict[str, Any] = {
                    "id": self.id,
                    "project_id": self.project_id,
                    "enabled": bool(self.enabled),
                    "embed_enabled": bool(self.enabled),
                    "is_enabled": self.is_enabled,
                    "allow_iframe": bool(self.allow_iframe),
                    "allowIframe": bool(self.allow_iframe),
                    "allow_public_embed": bool(self.allow_public_embed),
                    "allowPublicEmbed": bool(self.allow_public_embed),
                    "is_public": self.is_public,
                    "mode": self.mode,
                    "default_mode": self.default_mode,
                    "defaultMode": self.default_mode,
                    "effective_mode": self.effective_mode,
                    "effectiveMode": self.effective_mode,
                    "allowed_modes": normalize_embed_modes(self.allowed_modes),
                    "allowedModes": normalize_embed_modes(self.allowed_modes),
                    "spectator_only": bool(self.spectator_only),
                    "spectatorOnly": bool(self.spectator_only),
                    "readonly": self.effective_readonly,
                    "read_only": self.effective_readonly,
                    "allow_interaction": self.effective_interactive,
                    "allowInteraction": self.effective_interactive,
                    "allow_map": bool(self.allow_map),
                    "allowMap": bool(self.allow_map),
                    "allow_editor3d": bool(self.allow_editor3d),
                    "allowEditor3d": bool(self.allow_editor3d),
                    "allow_2d": bool(self.allow_2d),
                    "allow2d": bool(self.allow_2d),
                    "allow_lv": bool(self.allow_lv),
                    "allowLv": bool(self.allow_lv),
                    "allow_versions": bool(self.allow_versions),
                    "allowVersions": bool(self.allow_versions),
                    "allow_downloads": bool(self.allow_downloads),
                    "allowDownloads": bool(self.allow_downloads),
                    "require_auth": bool(self.require_auth),
                    "requireAuth": bool(self.require_auth),
                    "require_project_permission": bool(self.require_project_permission),
                    "requireProjectPermission": bool(self.require_project_permission),
                    "require_token": bool(self.require_token),
                    "requireToken": bool(self.require_token),
                    "token_label": self.token_label,
                    "tokenLabel": self.token_label,
                    "token_expires_at": isoformat(self.token_expires_at),
                    "tokenExpiresAt": isoformat(self.token_expires_at),
                    "token_expired": _datetime_expired(self.token_expires_at),
                    "allowed_origins": normalize_origins(self.allowed_origins),
                    "allowedOrigins": normalize_origins(self.allowed_origins),
                    "denied_origins": normalize_origins(self.denied_origins),
                    "deniedOrigins": normalize_origins(self.denied_origins),
                    "max_session_seconds": self.max_session_seconds,
                    "maxSessionSeconds": self.max_session_seconds,
                    "max_width": self.max_width,
                    "maxWidth": self.max_width,
                    "max_height": self.max_height,
                    "maxHeight": self.max_height,
                    "status": self.status,
                    "created_at": isoformat(self.created_at),
                    "updated_at": isoformat(self.updated_at),
                }

                if include_private:
                    payload["token_hash"] = self.token_hash
                    payload["created_by_user_id"] = self.created_by_user_id
                    payload["updated_by_user_id"] = self.updated_by_user_id
                    payload["settings"] = safe_dict(self.settings)
                    payload["metadata"] = safe_dict(self.metadata_json)

                return payload

            except Exception:
                return {
                    "id": getattr(self, "id", None),
                    "project_id": getattr(self, "project_id", None),
                    "enabled": bool(getattr(self, "enabled", False)),
                    "mode": getattr(self, "mode", DEFAULT_EMBED_MODE),
                }

        @classmethod
        def build(
            cls,
            *,
            project_id: Any,
            mode: Any = DEFAULT_EMBED_MODE,
            created_by_user_id: Optional[int] = None,
            payload: Optional[Mapping[str, Any]] = None,
        ) -> "ProjectEmbedPolicy":
            policy = cls()
            policy.project_id = safe_int(project_id, 0, minimum=1)
            policy.created_by_user_id = safe_int(created_by_user_id, 0) or None
            policy.updated_by_user_id = safe_int(created_by_user_id, 0) or None
            policy.enabled = True
            policy.allow_iframe = True
            policy.allow_public_embed = False
            policy.mode = normalize_embed_mode(mode, DEFAULT_EMBED_MODE)
            policy.default_mode = policy.mode
            policy.allowed_modes = normalize_embed_modes([policy.mode, *DEFAULT_ALLOWED_MODES])
            policy.spectator_only = policy.mode == EMBED_MODE_SPECTATOR
            policy.readonly = mode_is_readonly(policy.mode)
            policy.allow_interaction = mode_allows_interaction(policy.mode)
            policy.require_auth = True
            policy.require_project_permission = True
            policy.require_token = False
            policy.allowed_origins = []
            policy.denied_origins = []
            policy.settings = {}
            policy.metadata_json = {}

            if payload:
                policy.update_from_payload(payload)

            policy.normalize()

            return policy

    return ProjectEmbedPolicy


ProjectEmbedPolicy = _resolve_model(
    "ProjectEmbedPolicy",
    "project_embed_policies",
    _define_project_embed_policy_model,
)


# ─────────────────────────────────────────────────────────────
# Convenience helpers
# ─────────────────────────────────────────────────────────────

def build_embed_policy(
    *,
    project_id: Any,
    mode: Any = DEFAULT_EMBED_MODE,
    created_by_user_id: Optional[int] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> ProjectEmbedPolicy:
    try:
        if hasattr(ProjectEmbedPolicy, "build"):
            return ProjectEmbedPolicy.build(
                project_id=project_id,
                mode=mode,
                created_by_user_id=created_by_user_id,
                payload=payload,
            )

        policy = ProjectEmbedPolicy()
        policy.project_id = safe_int(project_id, 0, minimum=1)
        policy.mode = normalize_embed_mode(mode, DEFAULT_EMBED_MODE)

        if hasattr(policy, "update_from_payload") and payload:
            policy.update_from_payload(payload)

        if hasattr(policy, "normalize"):
            policy.normalize()

        return policy

    except Exception:
        policy = ProjectEmbedPolicy()
        try:
            policy.project_id = safe_int(project_id, 0)
            policy.mode = normalize_embed_mode(mode, DEFAULT_EMBED_MODE)
        except Exception:
            pass
        return policy


def get_embed_policy_by_project_id(project_id: Any) -> Optional[ProjectEmbedPolicy]:
    try:
        resolved_project_id = safe_int(project_id, 0, minimum=1)

        if not resolved_project_id:
            return None

        return ProjectEmbedPolicy.query.filter_by(project_id=resolved_project_id).one_or_none()

    except Exception:
        return None


def get_or_create_embed_policy(
    project_id: Any,
    *,
    created_by_user_id: Optional[int] = None,
    commit: bool = True,
) -> ProjectEmbedPolicy:
    resolved_project_id = safe_int(project_id, 0, minimum=1)

    try:
        policy = get_embed_policy_by_project_id(resolved_project_id)

        if policy is not None:
            return policy

        policy = build_embed_policy(
            project_id=resolved_project_id,
            created_by_user_id=created_by_user_id,
        )

        db.session.add(policy)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return policy

    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

        return build_embed_policy(
            project_id=resolved_project_id,
            created_by_user_id=created_by_user_id,
        )


def serialize_embed_policy(policy: Any, *, include_private: bool = False) -> Dict[str, Any]:
    try:
        if policy is None:
            return {}

        if hasattr(policy, "to_dict"):
            try:
                return policy.to_dict(include_private=include_private)
            except TypeError:
                return policy.to_dict()

        return {
            "id": getattr(policy, "id", None),
            "project_id": getattr(policy, "project_id", None),
            "enabled": bool(getattr(policy, "enabled", False)),
            "mode": getattr(policy, "mode", DEFAULT_EMBED_MODE),
        }

    except Exception:
        return {}


def update_embed_policy(
    policy: Any,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    updated_by_user_id: Optional[int] = None,
) -> Any:
    try:
        if policy is None:
            return None

        data = safe_dict(payload)

        if updated_by_user_id is not None:
            data["updated_by_user_id"] = updated_by_user_id

        if hasattr(policy, "update_from_payload"):
            policy.update_from_payload(data)
        else:
            for key, value in data.items():
                try:
                    if hasattr(policy, key):
                        setattr(policy, key, value)
                except Exception:
                    continue

        if hasattr(policy, "normalize"):
            policy.normalize()

        return policy

    except Exception:
        return policy


def get_project_embed_model_classes() -> List[Any]:
    return [ProjectEmbedPolicy]


def get_project_embed_model_status() -> Dict[str, Any]:
    try:
        count = -1

        try:
            count = int(ProjectEmbedPolicy.query.count())
        except Exception:
            count = -1

        return {
            "ok": True,
            "models": ["ProjectEmbedPolicy"],
            "tables": [getattr(ProjectEmbedPolicy, "__tablename__", "project_embed_policies")],
            "count": count,
            "modes": sorted(EMBED_MODES),
        }

    except Exception as exc:
        return {
            "ok": False,
            "models": ["ProjectEmbedPolicy"],
            "tables": ["project_embed_policies"],
            "error": str(exc),
        }


__all__ = [
    "EMBED_MODE_SPECTATOR",
    "EMBED_MODE_READONLY",
    "EMBED_MODE_INTERACTIVE",
    "EMBED_MODE_EDITOR",
    "EMBED_MODE_ADMIN",
    "EMBED_MODES",
    "DEFAULT_EMBED_MODE",
    "DEFAULT_ALLOWED_MODES",
    "ProjectEmbedPolicy",
    "normalize_embed_mode",
    "normalize_embed_modes",
    "normalize_origin",
    "normalize_origins",
    "mode_is_readonly",
    "mode_allows_interaction",
    "build_embed_policy",
    "get_embed_policy_by_project_id",
    "get_or_create_embed_policy",
    "serialize_embed_policy",
    "update_embed_policy",
    "get_project_embed_model_classes",
    "get_project_embed_model_status",
]