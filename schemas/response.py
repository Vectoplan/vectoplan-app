# /services/ChatAI/schemas/response.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union, Literal

try:
    from pydantic import BaseModel, Field, root_validator, validator
except Exception as _ex:  # harte Abhängigkeit transparent machen
    raise RuntimeError(f"pydantic required for ChatAI schemas: {_ex}") from _ex


# ───────────────────────── Actions ─────────────────────────

class _ActionBase(BaseModel):
    type: Literal["post_message", "update_state", "trigger_job"] = Field(..., description="Action-Typ")


class PostMessageAction(_ActionBase):
    type: Literal["post_message"] = "post_message"
    template: str = Field(..., min_length=1, description="Template-Key")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Payload für Template")
    role: Optional[str] = Field(default="service", description="Nachrichtenrolle")
    trace: Optional[List[str]] = Field(default=None, description="Trace-Pfad")

    @validator("role", pre=True, always=True)
    def _role_norm(cls, v: Optional[str]) -> str:
        try:
            vv = (v or "service").strip().lower()
            return vv if vv in {"service", "assistant", "system"} else "service"
        except Exception:
            return "service"


class UpdateStateAction(_ActionBase):
    type: Literal["update_state"] = "update_state"
    patch: Dict[str, Any] = Field(default_factory=dict, description="Merge-Patch")

    @validator("patch", pre=True, always=True)
    def _patch_norm(cls, v: Any) -> Dict[str, Any]:
        try:
            return dict(v or {})
        except Exception:
            return {}


class TriggerJobAction(_ActionBase):
    type: Literal["trigger_job"] = "trigger_job"
    service: Optional[str] = Field(default=None, description="Zielservice (optional)")
    op: str = Field(..., min_length=1, description="Operation")
    args: Dict[str, Any] = Field(default_factory=dict, description="Argumente")

    @validator("args", pre=True, always=True)
    def _args_norm(cls, v: Any) -> Dict[str, Any]:
        try:
            return dict(v or {})
        except Exception:
            return {}


Action = Union[PostMessageAction, UpdateStateAction, TriggerJobAction]


# ───────────────────────── Haupt-Response ─────────────────────────

class ChatAIResponse(BaseModel):
    """
    Einheitlicher Antwortvertrag der ChatAI an /services/app.
    """
    status: Literal["ok", "need_info", "error"] = Field("ok", description="Gesamtstatus")
    intent: Optional[str] = Field(default=None, description="Intent-Schlüssel")
    text: Optional[str] = Field(default=None, description="Freitext-Reply (optional)")
    explain: Optional[str] = Field(default=None, description="Kurze technische Begründung")

    slots: Dict[str, Any] = Field(default_factory=dict, description="Gefüllte Slots")
    missing: List[str] = Field(default_factory=list, description="Fehlende Pflicht-Slots")

    actions: List[Action] = Field(default_factory=list, description="Auszuführende Aktionen")

    # Lockerere Annahmen für Upstream-Outputs
    @validator("intent", pre=True)
    def _intent_norm(cls, v: Any) -> Optional[str]:
        try:
            s = str(v or "").strip()
            return s or None
        except Exception:
            return None

    @validator("status", pre=True)
    def _status_norm(cls, v: Any) -> str:
        try:
            s = str(v or "").strip().lower()
            if s in {"ok", "need_info", "error"}:
                return s
            # Heuristik
            if s in {"needinfo", "need-info", "missing", "ask"}:
                return "need_info"
            return "ok"
        except Exception:
            return "ok"

    @validator("slots", pre=True, always=True)
    def _slots_norm(cls, v: Any) -> Dict[str, Any]:
        try:
            return dict(v or {})
        except Exception:
            return {}

    @validator("missing", pre=True, always=True)
    def _missing_norm(cls, v: Any) -> List[str]:
        try:
            if v is None:
                return []
            if isinstance(v, (tuple, list)):
                return [str(x).strip() for x in v if str(x).strip()]
            # akzeptiere kommaseparierte Strings
            if isinstance(v, str):
                return [s.strip() for s in v.split(",") if s.strip()]
            return []
        except Exception:
            return []

    @validator("actions", pre=True, always=True)
    def _actions_norm(cls, v: Any) -> List[Dict[str, Any]]:
        try:
            if v is None:
                return []
            if isinstance(v, list):
                # Validierung der Elemente folgt in parse_actions()
                return v
            return []
        except Exception:
            return []

    @root_validator(pre=False)
    def _normalize_missing_vs_slots(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        try:
            missing = list(values.get("missing") or [])
            slots = dict(values.get("slots") or {})
            if missing and slots:
                # Felder, die inzwischen im slots vorhanden sind, nicht mehr als missing melden
                missing = [m for m in missing if m not in slots or slots.get(m) in (None, "", [], {})]
            values["missing"] = missing
            return values
        except Exception:
            return values

    # ── Hilfen ──

    @staticmethod
    def parse_actions(raw: List[Any]) -> List[Action]:
        out: List[Action] = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            t = str(item.get("type") or "").strip().lower()
            try:
                if t == "post_message":
                    out.append(PostMessageAction(**item))
                elif t == "update_state":
                    out.append(UpdateStateAction(**item))
                elif t == "trigger_job":
                    out.append(TriggerJobAction(**item))
            except Exception:
                # fehlerhafte Action ignorieren
                continue
        return out

    @classmethod
    def from_raw(cls, raw: Any) -> "ChatAIResponse":
        """
        Tolerantes Parsing aus beliebigen LLM/Tool-Ausgaben.
        - Strings -> text
        - Dicts   -> Felder per Schlüssel, Actions robust geparst
        - Listen  -> join als text
        """
        try:
            if raw is None:
                return cls()

            if isinstance(raw, str):
                return cls(text=raw)

            if isinstance(raw, bytes):
                try:
                    return cls(text=raw.decode("utf-8", errors="ignore"))
                except Exception:
                    return cls(text="")

            if isinstance(raw, list):
                # Falls Liste aus Strings -> zusammenfügen
                if all(isinstance(x, str) for x in raw):
                    return cls(text="\n".join(raw))
                # Liste aus dicts ohne Standardvertrag → als Text serialisieren
                try:
                    import json as _json  # lazy
                    return cls(text=_json.dumps(raw, ensure_ascii=False))
                except Exception:
                    return cls(text=str(raw))

            if isinstance(raw, dict):
                # Roh übernehmen und Actions separat parsen
                d = dict(raw)
                acts = cls.parse_actions(d.get("actions") if isinstance(d.get("actions"), list) else [])
                d["actions"] = acts
                return cls(**d)

            # Fallback
            return cls(text=str(raw))
        except Exception:
            # letzte Rettung
            return cls(text=str(raw))

    def to_http(self) -> Dict[str, Any]:
        """
        Sauberes, serialisierbares Dict für HTTP-Response.
        """
        try:
            return {
                "status": self.status,
                "intent": self.intent,
                "text": self.text,
                "explain": self.explain,
                "slots": dict(self.slots or {}),
                "missing": list(self.missing or []),
                "actions": [a.dict() for a in (self.actions or [])],
            }
        except Exception:
            # minimaler Fallback
            return {"status": "ok", "text": self.text or ""}
