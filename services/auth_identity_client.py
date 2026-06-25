# services/vectoplan-app/services/auth_identity_client.py
"""
VECTOPLAN auth identity client.

Zweck:
- Adapter-Schicht von vectoplan-app zum späteren Auth-/Registrierungsdienst.
- Prüft, ob eine E-Mail-Adresse als Account im externen Auth-System registriert ist.
- Liefert Auth-Identitätsdaten zurück, ohne in vectoplan-app echte User anzulegen.
- Stellt einen robusten Platzhaltermodus für die aktuelle Entwicklungsphase bereit.
- Kann später ohne großen Umbau an den echten Login-/Registrierungscontainer angebunden werden.

Wichtige Architekturregel:
- vectoplan-app erzeugt KEINE echten Benutzeraccounts.
- vectoplan-app verwaltet Projektrollen, Sichtbarkeit, Einladungen und Projektmitgliedschaften.
- Registrierung, Login, Account-Status, Abo-Status und Bigdata-Zugriff gehören zum Auth-/Registrierungsdienst.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Tuple

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - requests sollte vorhanden sein, aber Client darf nicht hart brechen.
    requests = None  # type: ignore

try:
    from flask import current_app, has_app_context
except Exception:  # pragma: no cover - Datei soll auch außerhalb Flask importierbar bleiben.
    current_app = None  # type: ignore

    def has_app_context() -> bool:  # type: ignore
        return False


EMAIL_RE = re.compile(
    r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$",
    re.IGNORECASE,
)

DEFAULT_LOOKUP_PATH = "/v1/auth/identity/lookup-email"
DEFAULT_INVITATION_DISPATCH_PATH = "/v1/auth/invitations/project"

DEFAULT_TIMEOUT_SECONDS = 4.0
DEFAULT_CACHE_TTL_SECONDS = 120
DEFAULT_NEGATIVE_CACHE_TTL_SECONDS = 30

DEFAULT_REGISTERED_EMAILS_ENV = "AUTH_IDENTITY_DEV_REGISTERED_EMAILS"

LOGGER_NAME = "vectoplan.auth_identity_client"


# ---------------------------------------------------------------------------
# Small safe helpers
# ---------------------------------------------------------------------------


def _logger() -> logging.Logger:
    try:
        if has_app_context() and current_app is not None:
            return current_app.logger  # type: ignore[union-attr]
    except Exception:
        pass
    return logging.getLogger(LOGGER_NAME)


def _log_debug(message: str, **extra: Any) -> None:
    try:
        _logger().debug("%s %s", message, _compact_json(extra) if extra else "")
    except Exception:
        pass


def _log_info(message: str, **extra: Any) -> None:
    try:
        _logger().info("%s %s", message, _compact_json(extra) if extra else "")
    except Exception:
        pass


def _log_warning(message: str, **extra: Any) -> None:
    try:
        _logger().warning("%s %s", message, _compact_json(extra) if extra else "")
    except Exception:
        pass


def _log_exception(message: str, **extra: Any) -> None:
    try:
        _logger().exception("%s %s", message, _compact_json(extra) if extra else "")
    except Exception:
        pass


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(value)


def _safe_str(value: Any, default: str = "", max_len: Optional[int] = None) -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        if max_len is not None and max_len > 0:
            return text[:max_len]
        return text
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled", "enable"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "disable"}:
            return False
        return default
    except Exception:
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, Mapping):
        try:
            return dict(value)
        except Exception:
            return {}
    return {}


def _safe_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _safe_str(value)
        if text:
            return text
    return ""


def _normalize_url_base(value: Any) -> str:
    text = _safe_str(value)
    if not text:
        return ""
    return text.rstrip("/")


def _join_url(base_url: str, path: str) -> str:
    base = _normalize_url_base(base_url)
    p = _safe_str(path)
    if not base:
        return ""
    if not p:
        return base
    return base + "/" + p.lstrip("/")


def _parse_csv_set(value: Any) -> set:
    try:
        if value is None:
            return set()
        if isinstance(value, (list, tuple, set)):
            return {
                _normalize_email(item)
                for item in value
                if _normalize_email(item)
            }
        text = str(value)
        parts = re.split(r"[,\n;\s]+", text)
        return {
            _normalize_email(part)
            for part in parts
            if _normalize_email(part)
        }
    except Exception:
        return set()


def _read_config(name: str, default: Any = None) -> Any:
    """
    Liest zuerst Flask current_app.config, dann Environment.

    Die Funktion ist absichtlich defensiv, damit Imports/Scripts/Tests nicht
    abbrechen, wenn kein Flask-App-Kontext aktiv ist.
    """
    try:
        if has_app_context() and current_app is not None:
            value = current_app.config.get(name)  # type: ignore[union-attr]
            if value is not None:
                return value
    except Exception:
        pass

    try:
        value = os.environ.get(name)
        if value is not None:
            return value
    except Exception:
        pass

    return default


def _read_first_config(names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        value = _read_config(name, None)
        if value is not None and _safe_str(value):
            return value
    return default


def _normalize_email(email: Any) -> str:
    text = _safe_str(email, default="", max_len=320).lower()
    if not text:
        return ""
    # Sehr einfache Normalisierung. Keine Gmail-spezifischen Regeln.
    return text


def is_valid_email(email: Any) -> bool:
    normalized = _normalize_email(email)
    if not normalized:
        return False
    if len(normalized) > 320:
        return False
    try:
        return EMAIL_RE.match(normalized) is not None
    except Exception:
        return False


def _dev_auth_user_id_for_email(email: str) -> str:
    normalized = _normalize_email(email)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:18]
    return "auth_dev_" + digest


def _display_name_from_email(email: str) -> str:
    normalized = _normalize_email(email)
    local_part = normalized.split("@", 1)[0] if "@" in normalized else normalized
    local_part = local_part.replace(".", " ").replace("_", " ").replace("-", " ")
    local_part = " ".join(part.capitalize() for part in local_part.split() if part)
    return local_part or normalized


def _extract_nested_bool(data: Mapping[str, Any], *paths: str, default: bool = False) -> bool:
    """
    Liest boolsche Werte aus flachen oder punktgetrennten Pfaden.

    Beispiele:
      can_use_bigdata
      features.bigdata
      account.bigdata_access
    """
    for path in paths:
        try:
            current: Any = data
            for segment in path.split("."):
                if not isinstance(current, Mapping):
                    current = None
                    break
                current = current.get(segment)
            if current is not None:
                return _safe_bool(current, default=default)
        except Exception:
            continue
    return default


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float

    def expired(self) -> bool:
        try:
            return time.time() >= self.expires_at
        except Exception:
            return True


class _TTLCache:
    """
    Kleiner Thread-sicherer In-Memory-TTL-Cache.

    Zweck:
    - Entlastet später den Auth-Service bei wiederholten E-Mail-Lookups.
    - Cacht auch negative Ergebnisse kurz, damit Tippfehler nicht sofort
      mehrfach externe Calls erzeugen.
    - Ist bewusst lokal und unverbindlich. Keine Persistenz, keine Wahrheit.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: Dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Tuple[bool, Any]:
        safe_key = _safe_str(key)
        if not safe_key:
            return False, None

        try:
            with self._lock:
                entry = self._items.get(safe_key)
                if entry is None:
                    return False, None
                if entry.expired():
                    self._items.pop(safe_key, None)
                    return False, None
                return True, entry.value
        except Exception:
            return False, None

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        safe_key = _safe_str(key)
        ttl = _safe_int(ttl_seconds, 0)
        if not safe_key or ttl <= 0:
            return

        try:
            expires_at = time.time() + ttl
            with self._lock:
                self._items[safe_key] = _CacheEntry(value=value, expires_at=expires_at)
        except Exception:
            pass

    def delete(self, key: str) -> None:
        safe_key = _safe_str(key)
        if not safe_key:
            return

        try:
            with self._lock:
                self._items.pop(safe_key, None)
        except Exception:
            pass

    def clear(self) -> None:
        try:
            with self._lock:
                self._items.clear()
        except Exception:
            pass

    def prune(self) -> None:
        try:
            with self._lock:
                expired_keys = [
                    key for key, entry in self._items.items()
                    if entry.expired()
                ]
                for key in expired_keys:
                    self._items.pop(key, None)
        except Exception:
            pass


_LOOKUP_CACHE = _TTLCache()
_CLIENT_LOCK = threading.RLock()
_CLIENT_SINGLETON: Optional["AuthIdentityClient"] = None


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


@dataclass
class AuthIdentityResult:
    """
    Ergebnis eines E-Mail-/Identity-Lookups.

    ok:
      Gibt an, ob die Anfrage technisch erfolgreich verarbeitet wurde.
      Ein nicht registrierter User ist für lookup_email() technisch ok,
      aber registered=False.

    registered:
      Gibt an, ob der Auth-/Registrierungsdienst die E-Mail als registrierten
      Account kennt.

    auth_user_id:
      Externe User-ID aus dem Auth-/Registrierungsdienst.
      Nicht identisch mit lokaler vectoplan-app User-ID.
    """

    ok: bool
    code: str
    email: str = ""
    registered: bool = False
    auth_user_id: Optional[str] = None
    display_name: Optional[str] = None
    account_plan: Optional[str] = None
    account_status: Optional[str] = None
    can_use_bigdata: bool = False
    source: str = "unknown"
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    status_code: Optional[int] = None
    cached: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "code": self.code,
            "email": self.email,
            "registered": bool(self.registered),
            "auth_user_id": self.auth_user_id,
            "display_name": self.display_name,
            "account_plan": self.account_plan,
            "account_status": self.account_status,
            "can_use_bigdata": bool(self.can_use_bigdata),
            "source": self.source,
            "message": self.message,
            "raw": self.raw,
            "error": self.error,
            "status_code": self.status_code,
            "cached": bool(self.cached),
        }

    @classmethod
    def invalid_email(cls, email: Any) -> "AuthIdentityResult":
        normalized = _normalize_email(email)
        return cls(
            ok=False,
            code="invalid_email",
            email=normalized,
            registered=False,
            source="validation",
            message="Die E-Mail-Adresse ist ungültig.",
        )

    @classmethod
    def not_registered(
        cls,
        email: Any,
        source: str = "auth_identity",
        message: str = "Für diese E-Mail-Adresse ist kein registrierter Account vorhanden.",
        raw: Optional[Dict[str, Any]] = None,
        status_code: Optional[int] = None,
    ) -> "AuthIdentityResult":
        return cls(
            ok=True,
            code="user_not_registered",
            email=_normalize_email(email),
            registered=False,
            source=source,
            message=message,
            raw=raw or {},
            status_code=status_code,
        )

    @classmethod
    def registered_identity(
        cls,
        email: Any,
        auth_user_id: str,
        display_name: Optional[str] = None,
        account_plan: Optional[str] = None,
        account_status: Optional[str] = None,
        can_use_bigdata: bool = False,
        source: str = "auth_identity",
        raw: Optional[Dict[str, Any]] = None,
        status_code: Optional[int] = None,
    ) -> "AuthIdentityResult":
        normalized = _normalize_email(email)
        return cls(
            ok=True,
            code="user_registered",
            email=normalized,
            registered=True,
            auth_user_id=_safe_str(auth_user_id, default=None),  # type: ignore[arg-type]
            display_name=_safe_str(display_name, default=None),  # type: ignore[arg-type]
            account_plan=_safe_str(account_plan, default=None),  # type: ignore[arg-type]
            account_status=_safe_str(account_status, default=None),  # type: ignore[arg-type]
            can_use_bigdata=bool(can_use_bigdata),
            source=source,
            message="Die E-Mail-Adresse gehört zu einem registrierten Account.",
            raw=raw or {},
            status_code=status_code,
        )

    @classmethod
    def unavailable(
        cls,
        email: Any,
        code: str = "auth_identity_unavailable",
        message: str = "Der Auth-/Registrierungsdienst ist nicht verfügbar.",
        source: str = "auth_identity",
        error: Optional[str] = None,
        status_code: Optional[int] = None,
        raw: Optional[Dict[str, Any]] = None,
    ) -> "AuthIdentityResult":
        return cls(
            ok=False,
            code=code,
            email=_normalize_email(email),
            registered=False,
            source=source,
            message=message,
            error=error,
            status_code=status_code,
            raw=raw or {},
        )


@dataclass
class AuthInvitationDispatchResult:
    """
    Ergebnis des externen Einladungsversands.

    Diese Klasse versendet/verwaltet keine ProjectInvitation in der App-DB.
    Das übernimmt später project_invitation_service.py.

    Dieser Client kann nur:
    - den späteren Auth-/Mail-Service anstoßen,
    - oder im aktuellen Entwicklungsmodus einen Platzhalter-Erfolg zurückgeben.
    """

    ok: bool
    code: str
    email: str = ""
    project_public_id: Optional[str] = None
    role: Optional[str] = None
    invitation_id: Optional[str] = None
    auth_user_id: Optional[str] = None
    external_sent: bool = False
    placeholder: bool = False
    source: str = "unknown"
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    status_code: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "code": self.code,
            "email": self.email,
            "project_public_id": self.project_public_id,
            "role": self.role,
            "invitation_id": self.invitation_id,
            "auth_user_id": self.auth_user_id,
            "external_sent": bool(self.external_sent),
            "placeholder": bool(self.placeholder),
            "source": self.source,
            "message": self.message,
            "raw": self.raw,
            "error": self.error,
            "status_code": self.status_code,
        }

    @classmethod
    def rejected_from_identity(
        cls,
        identity: AuthIdentityResult,
        project_public_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> "AuthInvitationDispatchResult":
        return cls(
            ok=False,
            code=identity.code,
            email=identity.email,
            project_public_id=project_public_id,
            role=role,
            auth_user_id=identity.auth_user_id,
            external_sent=False,
            placeholder=False,
            source=identity.source,
            message=identity.message,
            raw=identity.raw,
            error=identity.error,
            status_code=identity.status_code,
        )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthIdentityClientConfig:
    """
    Runtime-Konfiguration.

    Unterstützte ENV-/Flask-Config-Werte:

    AUTH_IDENTITY_INTERNAL_URL
      Bevorzugte interne URL des Auth-/Registrierungsdienstes.

    VECTOPLAN_AUTH_INTERNAL_URL
    AUTH_SERVICE_INTERNAL_URL
    REGISTRATION_INTERNAL_URL
      Fallback-Namen, damit die Datei robust in bestehende ENV-Strukturen passt.

    AUTH_IDENTITY_LOOKUP_PATH
      Lookup-Pfad. Default: /v1/auth/identity/lookup-email

    AUTH_IDENTITY_INVITATION_DISPATCH_PATH
      Pfad für späteren externen Einladungsversand.

    AUTH_IDENTITY_API_TOKEN
      Optionaler Bearer-Token für server-to-server Calls.

    AUTH_IDENTITY_DEV_MODE
      Aktiviert Dev-Fallback, wenn kein echter Auth-Service vorhanden ist.

    AUTH_IDENTITY_DEV_REGISTERED_EMAILS
      Kommagetrennte E-Mail-Allowlist für den Dev-Modus.
      Nur diese Adressen gelten als registriert, sofern ACCEPT_ALL nicht aktiv ist.

    AUTH_IDENTITY_DEV_ACCEPT_ALL_REGISTERED
      Nur für lokale Entwicklung. Wenn true, gilt jede syntaktisch valide E-Mail
      als registriert. Standard: false.

    AUTH_IDENTITY_PLACEHOLDER_INVITES
      Wenn true, gibt dispatch_project_invitation() im Dev-Modus einen
      Platzhalter-Erfolg zurück.
    """

    base_url: str = ""
    lookup_path: str = DEFAULT_LOOKUP_PATH
    invitation_dispatch_path: str = DEFAULT_INVITATION_DISPATCH_PATH
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    verify_ssl: bool = True
    api_token: str = ""
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    negative_cache_ttl_seconds: int = DEFAULT_NEGATIVE_CACHE_TTL_SECONDS
    dev_mode: bool = True
    dev_registered_emails: frozenset = field(default_factory=frozenset)
    dev_accept_all_registered: bool = False
    placeholder_invites_enabled: bool = True
    service_name: str = "auth_identity"

    @classmethod
    def from_runtime(cls) -> "AuthIdentityClientConfig":
        base_url = _normalize_url_base(
            _read_first_config(
                [
                    "AUTH_IDENTITY_INTERNAL_URL",
                    "VECTOPLAN_AUTH_INTERNAL_URL",
                    "AUTH_SERVICE_INTERNAL_URL",
                    "REGISTRATION_INTERNAL_URL",
                ],
                default="",
            )
        )

        explicit_dev_mode = _read_config("AUTH_IDENTITY_DEV_MODE", None)
        if explicit_dev_mode is None:
            # Solange noch kein Auth-Service konfiguriert ist, ist Dev-Modus sinnvoll.
            dev_mode = not bool(base_url)
        else:
            dev_mode = _safe_bool(explicit_dev_mode, default=not bool(base_url))

        dev_registered_emails = frozenset(
            _parse_csv_set(
                _read_config(
                    DEFAULT_REGISTERED_EMAILS_ENV,
                    "",
                )
            )
        )

        return cls(
            base_url=base_url,
            lookup_path=_safe_str(
                _read_config("AUTH_IDENTITY_LOOKUP_PATH", DEFAULT_LOOKUP_PATH),
                default=DEFAULT_LOOKUP_PATH,
            ),
            invitation_dispatch_path=_safe_str(
                _read_config(
                    "AUTH_IDENTITY_INVITATION_DISPATCH_PATH",
                    DEFAULT_INVITATION_DISPATCH_PATH,
                ),
                default=DEFAULT_INVITATION_DISPATCH_PATH,
            ),
            timeout_seconds=_safe_float(
                _read_config("AUTH_IDENTITY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
                default=DEFAULT_TIMEOUT_SECONDS,
            ),
            verify_ssl=_safe_bool(
                _read_config("AUTH_IDENTITY_VERIFY_SSL", True),
                default=True,
            ),
            api_token=_safe_str(
                _read_config("AUTH_IDENTITY_API_TOKEN", ""),
                default="",
            ),
            cache_ttl_seconds=_safe_int(
                _read_config("AUTH_IDENTITY_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS),
                default=DEFAULT_CACHE_TTL_SECONDS,
            ),
            negative_cache_ttl_seconds=_safe_int(
                _read_config(
                    "AUTH_IDENTITY_NEGATIVE_CACHE_TTL_SECONDS",
                    DEFAULT_NEGATIVE_CACHE_TTL_SECONDS,
                ),
                default=DEFAULT_NEGATIVE_CACHE_TTL_SECONDS,
            ),
            dev_mode=dev_mode,
            dev_registered_emails=dev_registered_emails,
            dev_accept_all_registered=_safe_bool(
                _read_config("AUTH_IDENTITY_DEV_ACCEPT_ALL_REGISTERED", False),
                default=False,
            ),
            placeholder_invites_enabled=_safe_bool(
                _read_config("AUTH_IDENTITY_PLACEHOLDER_INVITES", True),
                default=True,
            ),
            service_name=_safe_str(
                _read_config("AUTH_IDENTITY_SERVICE_NAME", "auth_identity"),
                default="auth_identity",
            ),
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AuthIdentityClient:
    """
    Robuster Client für den späteren Auth-/Registrierungsdienst.

    Dieser Client ist bewusst streng:
    - Bei Einladungen werden nicht registrierte E-Mails nicht akzeptiert.
    - Ohne Auth-Service werden nur konfigurierte Dev-Allowlist-Adressen als
      registriert behandelt.
    - Es werden keine lokalen AppUser erzeugt.
    """

    def __init__(self, config: Optional[AuthIdentityClientConfig] = None) -> None:
        self.config = config or AuthIdentityClientConfig.from_runtime()

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """
        Liefert einen ungefährlichen Status für Health-/Debug-Zwecke.
        Keine Tokens, keine Secrets.
        """
        cfg = self.config
        return {
            "ok": True,
            "service": cfg.service_name,
            "base_url_configured": bool(cfg.base_url),
            "lookup_path": cfg.lookup_path,
            "invitation_dispatch_path": cfg.invitation_dispatch_path,
            "dev_mode": bool(cfg.dev_mode),
            "dev_registered_email_count": len(cfg.dev_registered_emails),
            "dev_accept_all_registered": bool(cfg.dev_accept_all_registered),
            "placeholder_invites_enabled": bool(cfg.placeholder_invites_enabled),
            "cache_ttl_seconds": cfg.cache_ttl_seconds,
            "negative_cache_ttl_seconds": cfg.negative_cache_ttl_seconds,
            "requests_available": requests is not None,
        }

    def lookup_email(
        self,
        email: Any,
        use_cache: bool = True,
        allow_dev_fallback: bool = True,
    ) -> AuthIdentityResult:
        """
        Prüft eine E-Mail-Adresse gegen den Auth-/Registrierungsdienst.

        Rückgabe:
        - registered=True, wenn der externe Auth-Dienst oder Dev-Fallback
          die E-Mail als registriert kennt.
        - registered=False mit code=user_not_registered, wenn nicht registriert.
        - ok=False bei technischer Nichtverfügbarkeit oder ungültiger E-Mail.

        WICHTIG:
        Diese Methode erzeugt keine lokalen User.
        """
        normalized_email = _normalize_email(email)

        if not is_valid_email(normalized_email):
            return AuthIdentityResult.invalid_email(normalized_email)

        cache_key = self._lookup_cache_key(normalized_email)
        if use_cache:
            hit, cached_value = _LOOKUP_CACHE.get(cache_key)
            if hit and isinstance(cached_value, AuthIdentityResult):
                try:
                    cached_value.cached = True
                except Exception:
                    pass
                return cached_value

        result: Optional[AuthIdentityResult] = None

        if self.config.base_url:
            result = self._lookup_email_remote(normalized_email)

            if result.ok:
                self._cache_lookup_result(cache_key, result)
                return result

            # Remote technisch nicht verfügbar.
            # In der aktuellen Entwicklungsphase darf optional auf Dev-Fallback
            # gefallen werden, aber nur wenn dev_mode aktiv ist.
            if allow_dev_fallback and self.config.dev_mode:
                _log_warning(
                    "auth identity remote lookup failed; falling back to dev lookup",
                    email=normalized_email,
                    code=result.code,
                    error=result.error,
                    status_code=result.status_code,
                )
                fallback = self._lookup_email_dev(normalized_email, source="dev_fallback")
                self._cache_lookup_result(cache_key, fallback)
                return fallback

            self._cache_lookup_result(cache_key, result)
            return result

        if self.config.dev_mode:
            result = self._lookup_email_dev(normalized_email, source="dev")
            self._cache_lookup_result(cache_key, result)
            return result

        result = AuthIdentityResult.unavailable(
            normalized_email,
            code="auth_identity_not_configured",
            message="Es ist kein Auth-/Registrierungsdienst konfiguriert.",
            source="config",
        )
        self._cache_lookup_result(cache_key, result)
        return result

    def require_registered_email(
        self,
        email: Any,
        use_cache: bool = True,
    ) -> AuthIdentityResult:
        """
        Convenience-Methode für Einladungsflüsse.

        Gibt ok=True nur zurück, wenn:
        - E-Mail syntaktisch gültig ist,
        - und die Adresse registriert ist.
        """
        identity = self.lookup_email(email=email, use_cache=use_cache)

        if not identity.ok:
            return identity

        if not identity.registered:
            return AuthIdentityResult(
                ok=False,
                code="user_not_registered",
                email=identity.email,
                registered=False,
                source=identity.source,
                message="Einladungen sind nur an bereits registrierte Accounts möglich.",
                raw=identity.raw,
                status_code=identity.status_code,
                cached=identity.cached,
            )

        return identity

    def dispatch_project_invitation(
        self,
        email: Any,
        project_public_id: Any,
        role: Any,
        invited_by_auth_user_id: Optional[str] = None,
        invitation_id: Optional[str] = None,
        invitation_url: Optional[str] = None,
        message: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        require_registered: bool = True,
    ) -> AuthInvitationDispatchResult:
        """
        Stößt später den externen Einladungsversand an.

        Aktuell:
        - prüft optional, ob die E-Mail registriert ist,
        - ruft bei konfiguriertem Auth-Service den Dispatch-Endpunkt auf,
        - liefert sonst im Dev-Modus einen robusten Platzhalter-Erfolg.

        WICHTIG:
        Diese Methode speichert keine ProjectInvitation in der App-DB.
        Das macht später project_invitation_service.py.
        """
        normalized_email = _normalize_email(email)
        safe_project_public_id = _safe_str(project_public_id, max_len=120)
        safe_role = _safe_str(role, default="viewer", max_len=40)

        if not is_valid_email(normalized_email):
            return AuthInvitationDispatchResult(
                ok=False,
                code="invalid_email",
                email=normalized_email,
                project_public_id=safe_project_public_id,
                role=safe_role,
                source="validation",
                message="Die E-Mail-Adresse ist ungültig.",
            )

        identity: Optional[AuthIdentityResult] = None

        if require_registered:
            identity = self.require_registered_email(normalized_email)
            if not identity.ok or not identity.registered:
                return AuthInvitationDispatchResult.rejected_from_identity(
                    identity,
                    project_public_id=safe_project_public_id,
                    role=safe_role,
                )

        if self.config.base_url:
            remote_result = self._dispatch_project_invitation_remote(
                email=normalized_email,
                project_public_id=safe_project_public_id,
                role=safe_role,
                invited_by_auth_user_id=invited_by_auth_user_id,
                invitation_id=invitation_id,
                invitation_url=invitation_url,
                message=message,
                metadata=metadata,
                identity=identity,
            )
            if remote_result.ok:
                return remote_result

            if not self.config.dev_mode and not self.config.placeholder_invites_enabled:
                return remote_result

            _log_warning(
                "auth invitation dispatch failed; using placeholder dispatch",
                email=normalized_email,
                project_public_id=safe_project_public_id,
                role=safe_role,
                code=remote_result.code,
                error=remote_result.error,
                status_code=remote_result.status_code,
            )

        if self.config.placeholder_invites_enabled:
            return self._dispatch_project_invitation_placeholder(
                email=normalized_email,
                project_public_id=safe_project_public_id,
                role=safe_role,
                invited_by_auth_user_id=invited_by_auth_user_id,
                invitation_id=invitation_id,
                invitation_url=invitation_url,
                message=message,
                metadata=metadata,
                identity=identity,
            )

        return AuthInvitationDispatchResult(
            ok=False,
            code="invitation_dispatch_not_configured",
            email=normalized_email,
            project_public_id=safe_project_public_id,
            role=safe_role,
            auth_user_id=identity.auth_user_id if identity else None,
            external_sent=False,
            placeholder=False,
            source="config",
            message="Kein Einladungsversand konfiguriert.",
        )

    def clear_cache(self) -> None:
        _LOOKUP_CACHE.clear()

    # ---------------------------------------------------------------------
    # Remote lookup
    # ---------------------------------------------------------------------

    def _lookup_email_remote(self, email: str) -> AuthIdentityResult:
        if requests is None:
            return AuthIdentityResult.unavailable(
                email,
                code="requests_unavailable",
                message="Python requests ist nicht verfügbar.",
                source="runtime",
            )

        url = _join_url(self.config.base_url, self.config.lookup_path)
        if not url:
            return AuthIdentityResult.unavailable(
                email,
                code="auth_identity_lookup_url_missing",
                message="Auth-Identity-Lookup-URL fehlt.",
                source="config",
            )

        payload = {"email": email}

        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_ssl,
            )
        except Exception as exc:
            return AuthIdentityResult.unavailable(
                email,
                code="auth_identity_request_failed",
                message="Der Auth-/Registrierungsdienst konnte nicht erreicht werden.",
                source="http",
                error=str(exc),
            )

        status_code = getattr(response, "status_code", None)

        try:
            data = response.json()
        except Exception:
            data = {}

        data = _safe_dict(data)

        if status_code in {404, 409}:
            code = _safe_str(data.get("code"), default="user_not_registered")
            if code in {"user_not_registered", "not_registered", "identity_not_found"}:
                return AuthIdentityResult.not_registered(
                    email,
                    source="remote",
                    raw=data,
                    status_code=status_code,
                )

        if status_code in {401, 403}:
            return AuthIdentityResult.unavailable(
                email,
                code="auth_identity_access_denied",
                message="Der Auth-/Registrierungsdienst hat den serverseitigen Zugriff verweigert.",
                source="remote",
                status_code=status_code,
                raw=data,
            )

        if status_code is not None and status_code >= 500:
            return AuthIdentityResult.unavailable(
                email,
                code="auth_identity_server_error",
                message="Der Auth-/Registrierungsdienst meldet einen Serverfehler.",
                source="remote",
                status_code=status_code,
                raw=data,
            )

        if status_code is not None and status_code >= 400:
            return AuthIdentityResult.unavailable(
                email,
                code=_safe_str(data.get("code"), default="auth_identity_http_error"),
                message=_safe_str(
                    data.get("message"),
                    default="Der Auth-/Registrierungsdienst meldet einen Fehler.",
                ),
                source="remote",
                status_code=status_code,
                raw=data,
            )

        try:
            return self._parse_lookup_payload(
                email=email,
                data=data,
                status_code=status_code,
                source="remote",
            )
        except Exception as exc:
            _log_exception(
                "failed to parse auth identity lookup response",
                email=email,
                status_code=status_code,
            )
            return AuthIdentityResult.unavailable(
                email,
                code="auth_identity_parse_failed",
                message="Die Antwort des Auth-/Registrierungsdienstes konnte nicht ausgewertet werden.",
                source="remote",
                error=str(exc),
                status_code=status_code,
                raw=data,
            )

    def _parse_lookup_payload(
        self,
        email: str,
        data: Mapping[str, Any],
        status_code: Optional[int],
        source: str,
    ) -> AuthIdentityResult:
        payload = _safe_dict(data)

        if _safe_bool(payload.get("ok"), default=True) is False:
            code = _safe_str(payload.get("code"), default="auth_identity_rejected")
            if code in {"user_not_registered", "not_registered", "identity_not_found"}:
                return AuthIdentityResult.not_registered(
                    email,
                    source=source,
                    raw=payload,
                    status_code=status_code,
                )
            return AuthIdentityResult.unavailable(
                email,
                code=code,
                message=_safe_str(
                    payload.get("message"),
                    default="Der Auth-/Registrierungsdienst hat die Anfrage abgelehnt.",
                ),
                source=source,
                raw=payload,
                status_code=status_code,
            )

        identity = _safe_dict(
            payload.get("identity")
            or payload.get("user")
            or payload.get("account")
            or {}
        )

        registered_raw = payload.get("registered")
        if registered_raw is None:
            registered_raw = identity.get("registered")

        registered = _safe_bool(registered_raw, default=False)

        auth_user_id = _first_non_empty(
            payload.get("auth_user_id"),
            payload.get("user_id"),
            payload.get("external_user_id"),
            identity.get("auth_user_id"),
            identity.get("user_id"),
            identity.get("id"),
            identity.get("public_id"),
        )

        if not registered and auth_user_id:
            registered = True

        if not registered:
            return AuthIdentityResult.not_registered(
                email=email,
                source=source,
                raw=payload,
                status_code=status_code,
            )

        if not auth_user_id:
            return AuthIdentityResult.unavailable(
                email,
                code="auth_identity_missing_auth_user_id",
                message="Der Auth-/Registrierungsdienst meldet einen User, aber keine auth_user_id.",
                source=source,
                raw=payload,
                status_code=status_code,
            )

        account_plan = _first_non_empty(
            payload.get("account_plan"),
            payload.get("plan"),
            payload.get("subscription_plan"),
            payload.get("tier"),
            identity.get("account_plan"),
            identity.get("plan"),
            identity.get("subscription_plan"),
            identity.get("tier"),
        )

        account_status = _first_non_empty(
            payload.get("account_status"),
            payload.get("status"),
            identity.get("account_status"),
            identity.get("status"),
        )

        display_name = _first_non_empty(
            payload.get("display_name"),
            payload.get("name"),
            identity.get("display_name"),
            identity.get("name"),
            _display_name_from_email(email),
        )

        can_use_bigdata = _extract_nested_bool(
            payload,
            "can_use_bigdata",
            "bigdata_access",
            "features.bigdata",
            "features.can_use_bigdata",
            "account.can_use_bigdata",
            "account.bigdata_access",
            default=False,
        )

        return AuthIdentityResult.registered_identity(
            email=email,
            auth_user_id=auth_user_id,
            display_name=display_name,
            account_plan=account_plan or None,
            account_status=account_status or None,
            can_use_bigdata=can_use_bigdata,
            source=source,
            raw=payload,
            status_code=status_code,
        )

    # ---------------------------------------------------------------------
    # Dev lookup
    # ---------------------------------------------------------------------

    def _lookup_email_dev(self, email: str, source: str = "dev") -> AuthIdentityResult:
        normalized = _normalize_email(email)

        if self.config.dev_accept_all_registered:
            return AuthIdentityResult.registered_identity(
                email=normalized,
                auth_user_id=_dev_auth_user_id_for_email(normalized),
                display_name=_display_name_from_email(normalized),
                account_plan="dev",
                account_status="active",
                can_use_bigdata=False,
                source=source,
                raw={
                    "dev": True,
                    "accept_all": True,
                    "warning": "AUTH_IDENTITY_DEV_ACCEPT_ALL_REGISTERED is enabled.",
                },
            )

        if normalized in self.config.dev_registered_emails:
            return AuthIdentityResult.registered_identity(
                email=normalized,
                auth_user_id=_dev_auth_user_id_for_email(normalized),
                display_name=_display_name_from_email(normalized),
                account_plan="dev",
                account_status="active",
                can_use_bigdata=False,
                source=source,
                raw={
                    "dev": True,
                    "registered_email_allowlist": True,
                },
            )

        return AuthIdentityResult.not_registered(
            email=normalized,
            source=source,
            message=(
                "Die E-Mail-Adresse ist im aktuellen Dev-/Platzhaltermodus "
                "nicht als registrierter Account hinterlegt."
            ),
            raw={
                "dev": True,
                "registered_email_allowlist": sorted(self.config.dev_registered_emails),
                "accept_all": False,
            },
        )

    # ---------------------------------------------------------------------
    # Remote invitation dispatch
    # ---------------------------------------------------------------------

    def _dispatch_project_invitation_remote(
        self,
        email: str,
        project_public_id: str,
        role: str,
        invited_by_auth_user_id: Optional[str],
        invitation_id: Optional[str],
        invitation_url: Optional[str],
        message: Optional[str],
        metadata: Optional[Mapping[str, Any]],
        identity: Optional[AuthIdentityResult],
    ) -> AuthInvitationDispatchResult:
        if requests is None:
            return AuthInvitationDispatchResult(
                ok=False,
                code="requests_unavailable",
                email=email,
                project_public_id=project_public_id,
                role=role,
                auth_user_id=identity.auth_user_id if identity else None,
                source="runtime",
                message="Python requests ist nicht verfügbar.",
            )

        url = _join_url(self.config.base_url, self.config.invitation_dispatch_path)
        if not url:
            return AuthInvitationDispatchResult(
                ok=False,
                code="invitation_dispatch_url_missing",
                email=email,
                project_public_id=project_public_id,
                role=role,
                auth_user_id=identity.auth_user_id if identity else None,
                source="config",
                message="Auth-Invitation-Dispatch-URL fehlt.",
            )

        payload: Dict[str, Any] = {
            "email": email,
            "project_public_id": project_public_id,
            "role": role,
            "invited_by_auth_user_id": invited_by_auth_user_id,
            "invitation_id": invitation_id,
            "invitation_url": invitation_url,
            "message": message,
            "metadata": _safe_dict(metadata),
        }

        if identity is not None:
            payload["auth_user_id"] = identity.auth_user_id
            payload["identity"] = identity.to_dict()

        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_ssl,
            )
        except Exception as exc:
            return AuthInvitationDispatchResult(
                ok=False,
                code="invitation_dispatch_request_failed",
                email=email,
                project_public_id=project_public_id,
                role=role,
                auth_user_id=identity.auth_user_id if identity else None,
                source="http",
                message="Der externe Einladungsdienst konnte nicht erreicht werden.",
                error=str(exc),
            )

        status_code = getattr(response, "status_code", None)

        try:
            data = response.json()
        except Exception:
            data = {}

        data = _safe_dict(data)

        if status_code is not None and status_code >= 400:
            return AuthInvitationDispatchResult(
                ok=False,
                code=_safe_str(data.get("code"), default="invitation_dispatch_http_error"),
                email=email,
                project_public_id=project_public_id,
                role=role,
                auth_user_id=identity.auth_user_id if identity else None,
                external_sent=False,
                placeholder=False,
                source="remote",
                message=_safe_str(
                    data.get("message"),
                    default="Der externe Einladungsdienst meldet einen Fehler.",
                ),
                raw=data,
                status_code=status_code,
            )

        ok = _safe_bool(data.get("ok"), default=True)
        if not ok:
            return AuthInvitationDispatchResult(
                ok=False,
                code=_safe_str(data.get("code"), default="invitation_dispatch_rejected"),
                email=email,
                project_public_id=project_public_id,
                role=role,
                auth_user_id=identity.auth_user_id if identity else None,
                external_sent=False,
                placeholder=False,
                source="remote",
                message=_safe_str(
                    data.get("message"),
                    default="Der externe Einladungsdienst hat die Anfrage abgelehnt.",
                ),
                raw=data,
                status_code=status_code,
            )

        returned_invitation_id = _first_non_empty(
            data.get("invitation_id"),
            data.get("id"),
            invitation_id,
        )

        returned_auth_user_id = _first_non_empty(
            data.get("auth_user_id"),
            identity.auth_user_id if identity else None,
        )

        return AuthInvitationDispatchResult(
            ok=True,
            code=_safe_str(data.get("code"), default="invitation_dispatched"),
            email=email,
            project_public_id=project_public_id,
            role=role,
            invitation_id=returned_invitation_id or None,
            auth_user_id=returned_auth_user_id or None,
            external_sent=True,
            placeholder=False,
            source="remote",
            message=_safe_str(
                data.get("message"),
                default="Die Einladung wurde an den externen Einladungsdienst übergeben.",
            ),
            raw=data,
            status_code=status_code,
        )

    def _dispatch_project_invitation_placeholder(
        self,
        email: str,
        project_public_id: str,
        role: str,
        invited_by_auth_user_id: Optional[str],
        invitation_id: Optional[str],
        invitation_url: Optional[str],
        message: Optional[str],
        metadata: Optional[Mapping[str, Any]],
        identity: Optional[AuthIdentityResult],
    ) -> AuthInvitationDispatchResult:
        generated_invitation_id = invitation_id or self._placeholder_invitation_id(
            email=email,
            project_public_id=project_public_id,
            role=role,
        )

        raw = {
            "placeholder": True,
            "email": email,
            "project_public_id": project_public_id,
            "role": role,
            "invited_by_auth_user_id": invited_by_auth_user_id,
            "invitation_url": invitation_url,
            "message_present": bool(_safe_str(message)),
            "metadata": _safe_dict(metadata),
            "note": (
                "Kein echter Mail-/Auth-Einladungsversand. "
                "Diese Antwort dient nur der aktuellen Entwicklungsphase."
            ),
        }

        if identity is not None:
            raw["identity"] = identity.to_dict()

        return AuthInvitationDispatchResult(
            ok=True,
            code="invitation_placeholder_dispatched",
            email=email,
            project_public_id=project_public_id,
            role=role,
            invitation_id=generated_invitation_id,
            auth_user_id=identity.auth_user_id if identity else None,
            external_sent=False,
            placeholder=True,
            source="placeholder",
            message=(
                "Die Einladung wurde im Platzhaltermodus akzeptiert. "
                "Es wurde noch keine echte E-Mail versendet."
            ),
            raw=raw,
        )

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Vectoplan-Service": "vectoplan-app",
        }

        token = _safe_str(self.config.api_token)
        if token:
            headers["Authorization"] = "Bearer " + token

        return headers

    def _lookup_cache_key(self, email: str) -> str:
        return "auth_identity:lookup:" + _normalize_email(email)

    def _cache_lookup_result(self, cache_key: str, result: AuthIdentityResult) -> None:
        try:
            ttl = (
                self.config.cache_ttl_seconds
                if result.ok and result.registered
                else self.config.negative_cache_ttl_seconds
            )
            if ttl <= 0:
                return

            # Im Cache keine mutable cached=True-Variante ablegen.
            cached_copy = AuthIdentityResult(
                ok=result.ok,
                code=result.code,
                email=result.email,
                registered=result.registered,
                auth_user_id=result.auth_user_id,
                display_name=result.display_name,
                account_plan=result.account_plan,
                account_status=result.account_status,
                can_use_bigdata=result.can_use_bigdata,
                source=result.source,
                message=result.message,
                raw=dict(result.raw or {}),
                error=result.error,
                status_code=result.status_code,
                cached=False,
            )
            _LOOKUP_CACHE.set(cache_key, cached_copy, ttl)
        except Exception:
            pass

    def _placeholder_invitation_id(
        self,
        email: str,
        project_public_id: str,
        role: str,
    ) -> str:
        seed = "|".join(
            [
                _normalize_email(email),
                _safe_str(project_public_id),
                _safe_str(role),
                str(int(time.time())),
            ]
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
        return "pinv_" + digest


# ---------------------------------------------------------------------------
# Module-level convenience API
# ---------------------------------------------------------------------------


def get_auth_identity_client(refresh: bool = False) -> AuthIdentityClient:
    """
    Liefert einen Singleton-Client.

    refresh=True ist nützlich für Tests oder wenn Config zur Laufzeit neu
    gelesen werden soll.
    """
    global _CLIENT_SINGLETON

    try:
        with _CLIENT_LOCK:
            if refresh or _CLIENT_SINGLETON is None:
                _CLIENT_SINGLETON = AuthIdentityClient()
            return _CLIENT_SINGLETON
    except Exception:
        # Fallback ohne Singleton, damit Aufrufer nicht hart brechen.
        return AuthIdentityClient()


def clear_auth_identity_cache() -> None:
    try:
        _LOOKUP_CACHE.clear()
    except Exception:
        pass


def get_auth_identity_status() -> Dict[str, Any]:
    try:
        return get_auth_identity_client().status()
    except Exception as exc:
        return {
            "ok": False,
            "code": "auth_identity_status_failed",
            "error": str(exc),
        }


def lookup_email_identity(
    email: Any,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Dict-Wrapper für Routes/Services, die keine Dataclass verwenden wollen.
    """
    try:
        result = get_auth_identity_client().lookup_email(
            email=email,
            use_cache=use_cache,
        )
        return result.to_dict()
    except Exception as exc:
        _log_exception("lookup_email_identity failed", email=_normalize_email(email))
        return AuthIdentityResult.unavailable(
            email=email,
            code="auth_identity_lookup_failed",
            message="Der Auth-Identity-Lookup ist fehlgeschlagen.",
            source="exception",
            error=str(exc),
        ).to_dict()


def require_registered_email_identity(
    email: Any,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Dict-Wrapper für Einladungsservices.
    """
    try:
        result = get_auth_identity_client().require_registered_email(
            email=email,
            use_cache=use_cache,
        )
        return result.to_dict()
    except Exception as exc:
        _log_exception("require_registered_email_identity failed", email=_normalize_email(email))
        return AuthIdentityResult.unavailable(
            email=email,
            code="auth_identity_lookup_failed",
            message="Die Registrierungsprüfung ist fehlgeschlagen.",
            source="exception",
            error=str(exc),
        ).to_dict()


def dispatch_project_invitation_identity(
    email: Any,
    project_public_id: Any,
    role: Any,
    invited_by_auth_user_id: Optional[str] = None,
    invitation_id: Optional[str] = None,
    invitation_url: Optional[str] = None,
    message: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    require_registered: bool = True,
) -> Dict[str, Any]:
    """
    Dict-Wrapper für späteren project_invitation_service.py.
    """
    try:
        result = get_auth_identity_client().dispatch_project_invitation(
            email=email,
            project_public_id=project_public_id,
            role=role,
            invited_by_auth_user_id=invited_by_auth_user_id,
            invitation_id=invitation_id,
            invitation_url=invitation_url,
            message=message,
            metadata=metadata,
            require_registered=require_registered,
        )
        return result.to_dict()
    except Exception as exc:
        _log_exception(
            "dispatch_project_invitation_identity failed",
            email=_normalize_email(email),
            project_public_id=_safe_str(project_public_id),
        )
        return AuthInvitationDispatchResult(
            ok=False,
            code="invitation_dispatch_failed",
            email=_normalize_email(email),
            project_public_id=_safe_str(project_public_id),
            role=_safe_str(role),
            source="exception",
            message="Der externe Einladungsversand ist fehlgeschlagen.",
            error=str(exc),
        ).to_dict()


__all__ = [
    "AuthIdentityClient",
    "AuthIdentityClientConfig",
    "AuthIdentityResult",
    "AuthInvitationDispatchResult",
    "clear_auth_identity_cache",
    "dispatch_project_invitation_identity",
    "get_auth_identity_client",
    "get_auth_identity_status",
    "is_valid_email",
    "lookup_email_identity",
    "require_registered_email_identity",
]