"""Casos de uso de cápsulas (F08). Orquesta dominio + puertos; no conoce el disco."""
from __future__ import annotations

from typing import Any

from ..domain.capsules import Capsule, Pointer, apply_patch, create, parse, render, staleness
from ..domain.models import DomainError
from ..domain.ports import CapsuleStore, Clock, FileHasher
from ..domain.scoring import estimate_tokens


class CapsuleService:
    def __init__(self, store: CapsuleStore, hasher: FileHasher, clock: Clock) -> None:
        self.store, self.hasher, self.clock = store, hasher, clock

    def pointer(self, ref: str) -> Pointer:
        """Completa el sha256 de un puntero leyendo el archivo actual (12 hex bastan para detectar cambios)."""
        p = Pointer.parse(ref)
        if p.sha256:
            return p
        digest = self.hasher.sha256(p.path)
        if digest is None:
            raise DomainError(f"el puntero no existe en disco: {p.path}")
        return Pointer(p.path, digest[:12], p.lines)

    def new(self, task_id: str, owner: str, estado: str, agent: str, **kw: Any) -> Capsule:
        if self.store.read(task_id) is not None:
            raise DomainError(f"la cápsula {task_id} ya existe; usar capsule-patch")
        kw["punteros"] = tuple(self.pointer(r) for r in kw.get("punteros", ()))
        cap = create(task_id, owner, estado, self.clock.now(), agent, **kw)
        self.store.write(task_id, render(cap), expected=None)
        return cap

    def get(self, task_id: str) -> tuple[Capsule, str]:
        text = self.store.read(task_id)
        if text is None:
            raise DomainError(f"no existe la cápsula {task_id}")
        return parse(text), text

    def show(self, task_id: str) -> dict[str, Any]:
        cap, text = self.get(task_id)
        hashes = {p.path: self.hasher.sha256(p.path) for p in cap.punteros}
        return {"capsule": cap.meta(), "tokens_est": estimate_tokens(text),
                "staleness": staleness(cap, self.clock.now(), hashes), "text": text}

    def patch(self, task_id: str, ops: list[dict[str, Any]], expected_rev: int, agent: str) -> Capsule:
        cap, text = self.get(task_id)
        for op in ops:
            if op.get("field") == "punteros" and op.get("op") == "add":
                op["value"] = self.pointer(str(op["value"]))
        new = apply_patch(cap, ops, expected_rev, agent, self.clock.now())
        self.store.write(task_id, render(new), expected=text)
        return new

    def list(self) -> list[dict[str, Any]]:
        out = []
        for tid in self.store.list_ids():
            try:
                cap, _ = self.get(tid)
                out.append({"task_id": tid, "status": cap.status, "owner": cap.owner, "rev": cap.rev,
                            "updated_at": cap.updated_at})
            except DomainError as exc:
                out.append({"task_id": tid, "error": str(exc)})
        return out
