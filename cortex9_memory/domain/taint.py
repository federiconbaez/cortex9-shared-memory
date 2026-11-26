"""F27/F23: procedencia, taint y detector determinista de inyección. Puro, sin I/O."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

from .models import DomainError


class TrustTier(IntEnum):
    """Nivel de confianza de la procedencia. Mayor es más confiable."""

    T0 = 0  # externo no confiable: web, mails, PDFs y repos de terceros
    T1 = 1  # inferido por una bot (resumen, deducción)
    T2 = 2  # verificado localmente o fuente primaria auditada
    T3 = 3  # dicho por el usuario directamente


def parse_tier(value: str | TrustTier) -> TrustTier:
    if isinstance(value, TrustTier):
        return value
    key = str(value).strip().upper()
    try:
        return TrustTier[key]
    except KeyError as exc:
        raise DomainError(f"trust_tier inválido {value!r}; usar T0..T3") from exc


_HIDDEN = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\U000e0000-\U000e007f]"
)
_MARKUP_TAGS = (
    r"system|system_reminder|system-reminder|user_query|user|assistant|tool_result|tool_use|"
    r"function_calls|invoke|parameter|antml:[a-z_]+|cursor_untrusted_data\w*|instructions?"
)
_FAKE_MARKUP = re.compile(rf"<\s*/?\s*(?:{_MARKUP_TAGS})\b[^>]*>", re.IGNORECASE)
_INJECTION = re.compile(
    r"|".join(
        [
            r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\b",
            r"\b(?:ignore|disregard)\s+(?:all\s+)?(?:your\s+)?instructions\b",
            r"\byou\s+are\s+now\b",
            r"\bnew\s+instructions\s*:",
            r"\bexfiltrat\w*",
            r"\bsend\s+(?:me\s+)?(?:the\s+|your\s+)?(?:password|token|credentials?|secret|api\s*key)\b",
            r"\bsend\s+\w+\s+(?:password|token|credentials?|secret)\b",
            r"\brun\s+(?:this|the)\s+(?:shell\s+)?command\b",
            r"\bignor[aá]r?\s+(?:las\s+|tus\s+)?instrucciones\b",
            r"\bolvid[aá]r?\s+(?:las\s+|tus\s+)?instrucciones\b",
            r"\benvi[aá]r?\s+(?:el|la|los|las)\s+(?:token|contraseña|clave|credenciales?)\b",
            r"\bejecut[aá]r?\s+(?:este|el\s+siguiente)\s+comando\b",
        ]
    ),
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Verdict:
    status: str  # clean | suspicious | blocked
    reasons: tuple[str, ...]
    tier: TrustTier

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


def assess(text: str, tier: TrustTier | str) -> Verdict:
    """Clasifica un texto antes de persistirlo o inyectarlo en contexto.

    - Unicode oculto o marcado falso de sistema: bloqueado para cualquier tier.
    - Frases imperativas de inyección: bloqueadas salvo que las diga el usuario (T3), que queda `suspicious`.
    """
    t = parse_tier(tier)
    reasons: list[str] = []
    if _HIDDEN.search(text):
        reasons.append("hidden_unicode")
    if _FAKE_MARKUP.search(text):
        reasons.append("fake_markup")
    hard = bool(reasons)
    if _INJECTION.search(text):
        reasons.append("injection_phrase")
    if hard:
        return Verdict("blocked", tuple(reasons), t)
    if reasons:
        return Verdict("suspicious" if t is TrustTier.T3 else "blocked", tuple(reasons), t)
    return Verdict("clean", (), t)


def derive(inputs: Iterable[tuple[TrustTier, bool]]) -> tuple[TrustTier, bool]:
    """Lo derivado hereda el tier mínimo; queda tainted si algún insumo lo está o es T0."""
    items = list(inputs)
    if not items:
        raise DomainError("derive necesita al menos un insumo")
    tier = min(t for t, _ in items)
    tainted = any(flag for _, flag in items) or tier is TrustTier.T0
    return tier, tainted


def enforce_fact_write(fact_type: str, text: str, tier: TrustTier | str, tainted: bool) -> Verdict:
    """I-TAINT: valida una escritura de hecho. Lanza DomainError si no se puede persistir."""
    v = assess(text, tier)
    if v.blocked:
        raise DomainError(f"texto bloqueado por taint-guard: {', '.join(v.reasons)}")
    if fact_type == "behavior" and (v.tier < TrustTier.T2 or tainted):
        raise DomainError("un hecho behavior exige trust_tier >= T2 y sin taint")
    return v


def neutralize(text: str) -> str:
    """Prepara texto recuperado para un prompt: quita invisibles y desarma marcado de sistema."""
    clean = _HIDDEN.sub("", text)
    return _FAKE_MARKUP.sub(lambda m: m.group(0).replace("<", "‹").replace(">", "›"), clean)
