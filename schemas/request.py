# /services/ChatAI/schemas/request.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import base64

try:
    from pydantic import BaseModel, Field, validator, root_validator
except Exception as _ex:
    raise RuntimeError(f"pydantic required for ChatAI schemas: {_ex}") from _ex


# Sicherheits-/Robustheits-Grenzen
MAX_ATTACHMENTS: int = 12
MAX_HISTORY_ITEMS: int = 50
MAX_BASE64_LEN: int = 50 * 1024 * 1024  # ~50MB an Zeichen; hartes Limit vermeiden, nur Drop


# ───────────────────────── Models ─────────────────────────

class AttachmentIn(BaseModel):
    """Attachment vom App-Backend (Datei oder Referenz)."""
    id: Optional[str] = Field(default=None, description="Serverseitige Blob-ID")
    filename: Optional[str] = Field(default=None)
    mime: Optional[str] = Field(default=None)
    size: Optional[int] = Field(default=None, ge=0)
    url: Optional[str] = Field(default=None, description="Optionaler Fetch-Pfad")
    base64: Optional[str] = Field(default=None, description="Inline-Inhalt, Base64")

    @validator("filename", "mime", "url", pre=True)
    def _norm_str(cls, v: Any) -> Optional[str]:
        try:
            s = str(v or "").strip()
            return s or None
        except Exception:
            return None

    @validator("base64", pre=True)
    def _trim_b64(cls, v: Any) -> Optional[str]:
        try:
            if v is None:
                return None
            s = str(v)
            # primitive Plausibilitätschecks
            if len(s) > MAX_BASE64_LEN:
                # Zu groß → nicht als Fehler werten, nur verwerfen
                return None
            if not s or any(ch.isspace() for ch in s[:64]):
                # offensichtlich kein pures Base64 → ignorieren
                return None
            return s
        except Exception:
            return None

    def try_decode(self) -> bytes:
        """Best-effort Base64-Decode; bei Fehlern leeres bytes."""
        try:
            if not self.base64:
                return b""
            # strip Data-URL-Präfixe, falls vorhanden
            b64 = self.base64
            if "," in b64 and ";base64" in b64.split(",", 1)[0]:
                b64 = b64.split(",", 1)[1]
            return base64.b64decode(b64, validate=False)
        except Exception:
            return b""


class HistoryMessage(BaseModel):
    """Vorherige Chatnachricht zur Kontextbildung."""
    role: str = Field(..., description="user|assistant|system|service")
    text: str = Field(default="", description="Klartext der Nachricht")
    attachments: List[AttachmentIn] = Field(default_factory=list)

    @validator("role", pre=True, always=True)
    def _role_norm(cls, v: Any) -> str:
        try:
            s = str(v or "").strip().lower()
            return s if s in {"user", "assistant", "system", "service"} else "user"
        except Exception:
            return "user"

    @validator("text", pre=True, always=True)
    def _text_norm(cls, v: Any) -> str:
        try:
            return str(v or "")
        except Exception:
            return ""


class ChatAIRequest(BaseModel):
    """
    Einheitliches Eingabeformat aus /services/app.
    Beispielpayload in der App:
      {
        "chat_id": "UUID-der-Conversation",
        "message": "Text",
        "coordinate": [0.0, 0.0],
        "project_id": 123,
        "chat_message_id": 1700000123,
        "attachments": [{ "id":"...", "filename":"...", "base64":"..." }]
      }
    Optional empfohlen:
      "history": [HistoryMessage, ...],
      "context": { ... }   # Slots, Projektkontext, Detektionen
    """
    chat_id: Optional[str] = Field(default=None, description="Conversation-ID aus der App")
    message: str = Field(default="", description="Nutzertext")
    coordinate: Optional[Tuple[float, float]] = Field(default=None, description="[x,y] oder [lon,lat]")
    project_id: Optional[int] = Field(default=None, ge=0)
    chat_message_id: Optional[int] = Field(default=None, ge=0)

    attachments: List[AttachmentIn] = Field(default_factory=list)
    history: List[HistoryMessage] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)

    @validator("chat_id", pre=True, always=True)
    def _chat_id_norm(cls, v: Any) -> Optional[str]:
        try:
            s = str(v or "").strip()
            # leere / "null" / "None" → None
            if not s or s.lower() in {"null", "none"}:
                return None
            # hartes Längenlimit gegen Log-Spam
            return s[:128]
        except Exception:
            return None

    @validator("message", pre=True, always=True)
    def _msg_norm(cls, v: Any) -> str:
        try:
            return str(v or "")
        except Exception:
            return ""

    @validator("coordinate", pre=True)
    def _coord_norm(cls, v: Any) -> Optional[Tuple[float, float]]:
        try:
            if v is None:
                return None
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                x = float(v[0]); y = float(v[1])
                return (x, y)
            # einzelne Zahl oder falsches Format ignorieren
            return None
        except Exception:
            return None

    @validator("attachments", pre=True, always=True)
    def _attachments_norm(cls, v: Any) -> List[Dict[str, Any]]:
        try:
            if v is None:
                return []
            if isinstance(v, list):
                # harte Obergrenze, um LLM-Prompts nicht zu fluten
                return list(v)[:MAX_ATTACHMENTS]
            return []
        except Exception:
            return []

    @validator("history", pre=True, always=True)
    def _history_norm(cls, v: Any) -> List[Dict[str, Any]]:
        try:
            if v is None:
                return []
            if isinstance(v, list):
                # nur die letzten N Einträge, Sequenz beibehalten
                return v[-MAX_HISTORY_ITEMS:]
            return []
        except Exception:
            return []

    @validator("context", pre=True, always=True)
    def _context_norm(cls, v: Any) -> Dict[str, Any]:
        try:
            return dict(v or {})
        except Exception:
            return {}

    # --------- Hilfsmethoden ---------

    def text_or_fallback(self) -> str:
        """Gibt message oder einen sinnvollen Fallback aus history zurück."""
        try:
            if self.message:
                return self.message
            # jüngste user- oder system-Nachricht als Fallback
            for m in reversed(self.history or []):
                if m and m.text and m.role in {"user", "system"}:
                    return m.text
            return ""
        except Exception:
            return ""

    def iter_attachments(self) -> List[AttachmentIn]:
        """Liste der Attachments, garantiert als AttachmentIn-Objekte."""
        try:
            return list(self.attachments or [])
        except Exception:
            return []

    def to_minimal_dict(self) -> Dict[str, Any]:
        """
        Minimale, seriell sichere Darstellung für Logs/Tracing.
        Base64 wird dabei verworfen.
        """
        try:
            atts = []
            for a in self.attachments or []:
                try:
                    atts.append({
                        "id": a.id, "filename": a.filename, "mime": a.mime, "size": a.size, "has_b64": bool(a.base64)
                    })
                except Exception:
                    continue
            hist = []
            for h in self.history or []:
                try:
                    hist.append({"role": h.role, "text": h.text[:160]})
                except Exception:
                    continue
            return {
                "chat_id": self.chat_id,
                "message": (self.message or "")[:500],
                "coordinate": list(self.coordinate) if self.coordinate else None,
                "project_id": self.project_id,
                "chat_message_id": self.chat_message_id,
                "attachments": atts,
                "history": hist,
                "context_keys": list((self.context or {}).keys()),
            }
        except Exception:
            return {"chat_id": self.chat_id, "message": (self.message or "")[:500]}

    @classmethod
    def from_raw(cls, raw: Any) -> "ChatAIRequest":
        """
        Tolerantes Parsing aus beliebigen Requests.
        """
        try:
            if isinstance(raw, dict):
                return cls(**raw)
            # Fallbacks für exotische Typen
            if raw is None:
                return cls()
            return cls(message=str(raw))
        except Exception:
            # letzte Rettung
            return cls()
