"""F50/F51: compactación de todo-list y cache de resultados por contenido. Puro."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import DomainError
from .scoring import estimate_tokens, normalize_text, parse_iso

_ITEM = re.compile(r"^\s*-\s*\*\*(?P<status>[A-Z_]+)\*\*\s*:")
ARCHIVE_STATUSES = ("COMPLETED", "CANCELLED", "DONE")


@dataclass(frozen=True)
class TodoCompaction:
    active_text: str
    archived_text: str
    n_active: int
    n_archived: int
    tokens_before: int
    tokens_after: int

    @property
    def n_items(self) -> int:
        return self.n_active + self.n_archived

    @property
    def tokens_saved(self) -> int:
        return self.tokens_before - self.tokens_after


def compact_todo(text: str) -> TodoCompaction:
    """I-TODO: activos + archivados == ítems de entrada; estados desconocidos quedan activos."""
    active: list[str] = []
    archived: list[str] = []
    n_act = 0
    for ln in text.splitlines():
        m = _ITEM.match(ln)
        if m and m.group("status") in ARCHIVE_STATUSES:
            archived.append(ln)
            continue
        if m:
            n_act += 1
        active.append(ln)
    out = "\n".join(active) + ("\n" if text.endswith("\n") else "")
    arch = "\n".join(archived) + ("\n" if archived else "")
    before = estimate_tokens(text) if text else 0
    after = estimate_tokens(out) if out else 0
    return TodoCompaction(out, arch, n_act, len(archived), before, after)


def cache_key(kind: str, query: str) -> str:
    if not kind.strip() or not query.strip():
        raise DomainError("kind y query no vacíos")
    return hashlib.sha256(f"{kind.strip().lower()}\0{normalize_text(query)}".encode()).hexdigest()


@dataclass(frozen=True)
class CacheEntry:
    key: str
    kind: str
    query: str
    created_at: str
    ttl_s: int
    pointers: Mapping[str, str] = field(default_factory=dict)
    tokens: int = 0

    def __post_init__(self) -> None:
        parse_iso(self.created_at)
        if self.ttl_s <= 0:
            raise DomainError("ttl_s > 0")
        if self.key != cache_key(self.kind, self.query):
            raise DomainError("key no coincide con kind+query")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pointers"] = dict(self.pointers)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CacheEntry:
        return cls(**dict(d))


def check_entry(e: CacheEntry, now: str, current: Mapping[str, str | None]) -> tuple[bool, str]:
    """I-CACHE: TTL vigente y todos los punteros con el mismo sha256."""
    if (parse_iso(now) - parse_iso(e.created_at)).total_seconds() > e.ttl_s:
        return False, "ttl vencido"
    changed = [p for p, h in e.pointers.items() if current.get(p) != h]
    if changed:
        return False, "insumos cambiados: " + ", ".join(sorted(changed))
    return True, "hit"
