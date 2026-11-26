"""Casos de uso de handoff por punteros (F06) con guarda de presupuesto y registro en el log."""
from __future__ import annotations

from typing import Any

from ..domain.capsules import Pointer
from ..domain.handoff import stale_inputs, validate_handoff, validate_result
from ..domain.models import DomainError
from ..domain.ports import FileHasher
from .budget import BudgetService
from .events import EventService


class HandoffService:
    def __init__(self, hasher: FileHasher, budget: BudgetService | None = None,
                 events: EventService | None = None) -> None:
        self.hasher, self.budget, self.events = hasher, budget, events

    def _pin(self, ref: str) -> str:
        p = Pointer.parse(ref)
        if p.sha256:
            return p.ref()
        digest = self.hasher.sha256(p.path)
        if digest is None:
            raise DomainError(f"el puntero no existe en disco: {p.path}")
        return Pointer(p.path, digest[:12], p.lines).ref()

    def prepare(self, raw: dict[str, Any]) -> dict[str, Any]:
        raw = dict(raw, inputs=[self._pin(r) for r in raw.get("inputs") or []])
        out = validate_handoff(raw)
        guard = out["envelope"]["budget_guard"]
        if self.budget is not None:
            st = self.budget.status()
            stop = float(guard.get("stop_if_weekly_pct_ge", 75))
            if st.pct is not None and st.pct >= stop:
                raise DomainError(f"aborted_budget: uso {st.pct}% >= {stop}% ({st.level})")
            if st.pct is not None and "max_delta_pct" in guard:
                adm = self.budget.admit("pesada" if stop <= 75 else "liviana", float(guard["max_delta_pct"]))
                if not adm["allowed"]:
                    raise DomainError(f"aborted_budget: {adm['reason']}")
            out["budget"] = {"level": st.level, "pct": st.pct}
        if self.events is not None:
            env = out["envelope"]
            self.events.append(env["from"], "handoff_sent", env["task_id"], {"id": env["id"], "to": env["to"]})
        return out

    def check(self, envelope: dict[str, Any]) -> dict[str, Any]:
        out = validate_handoff(envelope)
        hashes = {Pointer.parse(r).path: self.hasher.sha256(Pointer.parse(r).path) for r in out["envelope"]["inputs"]}
        stale = stale_inputs(out["envelope"], hashes)
        return {"ok": not stale, "stale_inputs": stale, "tokens_est": out["tokens_est"]}

    def result(self, raw: dict[str, Any]) -> dict[str, Any]:
        raw = dict(raw, artifacts=[self._pin(r) for r in raw.get("artifacts") or []])
        out = validate_result(raw)
        if self.events is not None:
            env = out["envelope"]
            self.events.append(env["from"], "handoff_ack", None, {"id": env["id"], "status": env["status"]})
        return out
