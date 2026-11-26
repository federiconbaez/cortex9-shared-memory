"""Casos de uso F50/F51: compactar todo-list y cache de resultados por contenido."""
from __future__ import annotations

import json
from typing import Any

from ..domain.context import CacheEntry, cache_key, check_entry
from ..domain.models import DomainError
from ..domain.ports import AppendLog, CacheStore, Clock, FileHasher
from ..domain.scoring import estimate_tokens


class CacheService:
    def __init__(self, store: CacheStore, hasher: FileHasher, stats: AppendLog, clock: Clock) -> None:
        self.store, self.hasher, self.stats_log, self.clock = store, hasher, stats, clock

    def _stat(self, rec: dict[str, Any]) -> None:
        line = json.dumps({"ts": self.clock.now(), **rec}, sort_keys=True) + "\n"
        self.stats_log.append_atomic(lambda cur: ("" if cur == "" or cur.endswith("\n") else "\n") + line)

    def put(self, kind: str, query: str, content: str, pointers: list[str], ttl_s: int = 86400) -> dict[str, Any]:
        hashes: dict[str, str] = {}
        for p in pointers:
            h = self.hasher.sha256(p)
            if h is None:
                raise DomainError(f"puntero inexistente: {p}")
            hashes[p] = h
        e = CacheEntry(cache_key(kind, query), kind, query, self.clock.now(), ttl_s, hashes,
                       estimate_tokens(content) if content else 0)
        self.store.write(e.key, json.dumps(e.to_dict(), sort_keys=True, ensure_ascii=False), content)
        self._stat({"op": "put", "key": e.key})
        return {"key": e.key, "tokens": e.tokens, "pointers": len(hashes)}

    def get(self, kind: str, query: str) -> tuple[str | None, str]:
        key = cache_key(kind, query)
        meta, content = self.store.read(key)
        if meta is None or content is None:
            self._stat({"op": "miss", "key": key, "why": "ausente"})
            return None, "ausente"
        try:
            e = CacheEntry.from_dict(json.loads(meta))
        except (json.JSONDecodeError, TypeError, DomainError):
            self._stat({"op": "miss", "key": key, "why": "metadatos inválidos"})
            return None, "metadatos inválidos"
        ok, why = check_entry(e, self.clock.now(), {p: self.hasher.sha256(p) for p in e.pointers})
        self._stat({"op": "hit" if ok else "miss", "key": key, "why": why, "tokens": e.tokens if ok else 0})
        return (content, why) if ok else (None, why)

    def stats(self) -> dict[str, Any]:
        hits = misses = puts = saved = 0
        for ln in self.stats_log.read_text().splitlines():
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            op = d.get("op")
            hits += op == "hit"
            misses += op == "miss"
            puts += op == "put"
            saved += int(d.get("tokens", 0)) if op == "hit" else 0
        total = hits + misses
        return {"hits": hits, "misses": misses, "puts": puts, "hit_rate": round(hits / total, 3) if total else None,
                "tokens_saved_est": saved}
