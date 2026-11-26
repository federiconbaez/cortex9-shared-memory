"""Casos de uso del semáforo de consumo (F31/F29). Lecturas en un JSONL compartido entre bots."""
from __future__ import annotations

import json
from typing import Any

from ..domain.budget import Admission, BudgetStatus, UsageSnapshot, admit, calibrate, evaluate, predict_delta
from ..domain.models import DomainError
from ..domain.ports import AppendLog, Clock


class BudgetService:
    def __init__(self, log: AppendLog, clock: Clock) -> None:
        self.log, self.clock = log, clock

    def snapshots(self) -> tuple[list[UsageSnapshot], int]:
        out, bad = [], 0
        for line in self.log.read_text().splitlines():
            if not line.strip():
                continue
            try:
                out.append(UsageSnapshot(**json.loads(line)))
            except (json.JSONDecodeError, TypeError, DomainError):
                bad += 1
        return out, bad

    def record(self, pct: float, reset_at: str, agent: str, **kw: Any) -> BudgetStatus:
        snap = UsageSnapshot(ts=kw.pop("ts", None) or self.clock.now(), pct=pct, reset_at=reset_at, agent=agent, **kw)
        line = json.dumps(snap.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
        self.log.append_atomic(lambda cur: ("" if cur == "" or cur.endswith("\n") else "\n") + line)
        return self.status()

    def status(self) -> BudgetStatus:
        snaps, _ = self.snapshots()
        return evaluate(snaps, self.clock.now())

    def admit(self, kind: str, est_delta_pct: float | None = None, tokens_est: int | None = None) -> dict[str, Any]:
        snaps, _ = self.snapshots()
        rate = calibrate(snaps)
        delta = est_delta_pct
        source = "declarado"
        if delta is None:
            delta = predict_delta(rate, tokens_est or 0)
            source = "calibrado"
        if delta is None:
            raise DomainError("sin calibración: pasar --delta (puntos %) o registrar pares before/after con tokens")
        st = evaluate(snaps, self.clock.now())
        a: Admission = admit(st, kind, delta)
        return {"allowed": a.allowed, "reason": a.reason, "headroom_pct": a.headroom_pct, "level": st.level,
                "pct": st.pct, "est_delta_pct": round(delta, 3), "delta_source": source, "rate_pct_per_1k": rate}
