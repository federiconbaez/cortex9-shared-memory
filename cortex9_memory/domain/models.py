"""Entidades y reglas puras. Sin dependencias de infraestructura."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

MEMORY_TYPES = ("world", "user_profile", "behavior", "workstream")
INSTRUMENT_KINDS = ("T", "R", "I", "F")
ENTITY_TYPES = ("agent", "session", "summary", "fact", "archetype", "instrument", "skill")
EVIDENCE_LABELS = ("alta", "media", "estimacion", "no_encontrado")
NOT_FOUND = "NO ENCONTRADO"
LABEL_DEFAULT_SCORE = {"alta": 0.9, "media": 0.7, "estimacion": 0.4, "no_encontrado": 0.0}

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ACRONYM = re.compile(r"^[TRIF][A-Z0-9]{1,11}(-FNBR)?$")


class DomainError(ValueError):
    """Violación de una invariante del CONTRACT."""


def require_slug(value: str, what: str) -> str:
    if not value or not _SLUG.match(value):
        raise DomainError(f"{what} debe ser kebab-case [a-z0-9-]: {value!r}")
    return value


def require_text(value: str | None, what: str) -> str:
    if value is None or not value.strip():
        raise DomainError(f"{what} no puede estar vacío")
    return value.strip()


def require_json(value: Any, what: str) -> str:
    """Acepta dict/list o string JSON; devuelve JSON canónico."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DomainError(f"{what} no es JSON válido: {exc}") from exc
    if not isinstance(parsed, (dict, list)):
        raise DomainError(f"{what} debe ser objeto o array JSON")
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def label_for_score(score: float) -> str:
    """Mapea confianza numérica a la escala del protocolo doctoral."""
    if score >= 0.85:
        return "alta"
    if score >= 0.6:
        return "media"
    if score > 0.0:
        return "estimacion"
    return "no_encontrado"


@dataclass(frozen=True)
class Agent:
    name: str
    role: str = ""
    id: int | None = None

    def __post_init__(self) -> None:
        require_slug(self.name, "agent.name")


@dataclass(frozen=True)
class Session:
    agent: str | None
    channel: str = "chat"
    title: str = ""
    summary: str = ""
    started_at: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        require_slug(self.channel, "session.channel")


@dataclass(frozen=True)
class MessageSummary:
    session_id: int
    turn_range: str
    summary: str
    tokens_est: int = 0
    agent: str | None = None

    def __post_init__(self) -> None:
        require_text(self.turn_range, "turn_range")
        require_text(self.summary, "summary")
        if self.tokens_est < 0:
            raise DomainError("tokens_est >= 0")

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class MemoryFact:
    type: str
    fact_text: str
    confidence: float | None = None
    evidence_label: str | None = None
    meta_json: str = "{}"
    valid_from: str | None = None
    valid_until: str | None = None
    source: str = ""
    agent: str | None = None
    session_id: int | None = None
    source_key: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        if self.type not in MEMORY_TYPES:
            raise DomainError(f"type inválido {self.type!r}; usar {MEMORY_TYPES}")
        require_text(self.fact_text, "fact_text")
        conf = LABEL_DEFAULT_SCORE.get(self.evidence_label or "", 0.8) if self.confidence is None else float(self.confidence)
        object.__setattr__(self, "confidence", conf)
        if not 0.0 <= conf <= 1.0:
            raise DomainError("confidence debe estar en [0,1]")
        if self.evidence_label is not None and self.evidence_label not in EVIDENCE_LABELS:
            raise DomainError(f"evidence_label inválido {self.evidence_label!r}")
        object.__setattr__(self, "meta_json", require_json(self.meta_json, "meta_json"))
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise DomainError("valid_until < valid_from")
        if self.source_key is not None and not self.source_key.strip():
            raise DomainError("source_key no puede ser cadena vacía")

    def resolved_label(self) -> str:
        return self.evidence_label or label_for_score(float(self.confidence or 0.0))


@dataclass(frozen=True)
class Archetype:
    slug: str
    name: str
    definition: str
    rules: list = field(default_factory=list)

    def __post_init__(self) -> None:
        require_slug(self.slug, "archetype.slug")
        require_text(self.name, "archetype.name")
        require_text(self.definition, "archetype.definition")


@dataclass(frozen=True)
class Instrument:
    acronym: str
    name: str
    kind: str
    definition: str
    refutation: str = NOT_FOUND
    related_workstream: str = ""
    source: str = ""
    agent: str | None = None

    def __post_init__(self) -> None:
        if not _ACRONYM.match(self.acronym):
            raise DomainError(f"acrónimo inválido {self.acronym!r} (ej. TEME-FNBR)")
        if self.kind not in INSTRUMENT_KINDS:
            raise DomainError(f"kind inválido {self.kind!r}")
        if self.acronym[0] != self.kind:
            raise DomainError("kind debe coincidir con la primera letra del acrónimo")
        require_text(self.name, "instrument.name")
        require_text(self.definition, "instrument.definition")
        require_text(self.refutation, "instrument.refutation")


@dataclass(frozen=True)
class Skill:
    slug: str
    title: str
    path_or_body: str
    version: str = "1"
    portable_path: str = ""
    sha256: str = ""

    def __post_init__(self) -> None:
        require_slug(self.slug, "skill.slug")
        require_text(self.title, "skill.title")
        require_text(self.path_or_body, "skill.path_or_body")


@dataclass(frozen=True)
class Relation:
    from_type: str
    from_id: int
    relation: str
    to_type: str
    to_id: int
    note: str = ""
    agent: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None

    def __post_init__(self) -> None:
        for t in (self.from_type, self.to_type):
            if t not in ENTITY_TYPES:
                raise DomainError(f"tipo de entidad desconocido {t!r}; usar {ENTITY_TYPES}")
        require_text(self.relation, "relation")
        if (self.from_type, self.from_id) == (self.to_type, self.to_id):
            raise DomainError("una entidad no puede relacionarse consigo misma")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise DomainError("valid_until < valid_from")
