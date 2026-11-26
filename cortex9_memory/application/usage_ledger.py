"""Casos de uso F39: ingesta de reportes de uso y velocidad de quema."""
from __future__ import annotations

import json
from typing import Any

from ..domain.models import DomainError
from ..domain.ports import AppendLog, Clock
from ..domain.usage_ledger import UsageReport, burn_rates, parse_report, project_exhaustion
from .budget import BudgetService


class LedgerService:
    def __init__(self, log: AppendLog, budget: BudgetService, clock: Clock) -> None:
        self.log, self.budget, self.clock = log, budget, clock

    def ingest(self, text: str, agent: str, reset_at: str | None = None) -> dict[str, Any]:
        rep = parse_report(text, self.clock.now())
        rec = {"agent": agent, **rep.to_dict()}
        line = json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n"
        self.log.append_atomic(lambda cur: ("" if cur == "" or cur.endswith("\n") else "\n") + line)
        recorded = False
        if reset_at and rep.pct_used is not None:
            self.budget.record(rep.pct_used, reset_at, agent, source="report")
            recorded = True
        return {"key": rep.key, "rows": len(rep.rows), "pct_used": rep.pct_used, "budget_recorded": recorded}

    def reports(self) -> tuple[list[UsageReport], int]:
        out, bad = [], 0
        for ln in self.log.read_text().splitlines():
            if not ln.strip():
                continue
            try:
                d = json.loads(ln)
                d.pop("agent", None)
                out.append(UsageReport.from_dict(d))
            except (json.JSONDecodeError, KeyError, TypeError, DomainError):
                bad += 1
        return out, bad

    def latest(self) -> dict[str, UsageReport]:
        """Último reporte por desglose (`periodo:dims`)."""
        reps, _ = self.reports()
        last: dict[str, UsageReport] = {}
        for r in reps:
            last[r.key] = r
        return last

    def burn(self) -> dict[str, Any]:
        snaps, _ = self.budget.snapshots()
        burns = burn_rates(snaps)
        st = self.budget.status()
        now = self.clock.now()
        active = [b for b in burns if b.reset_at == st.reset_at]
        proj = None
        if active and st.pct is not None and st.reset_at:
            p = project_exhaustion(st.pct, active[-1].pts_per_hour, now, st.reset_at)
            proj = {"hours_to_full": None if p.hours_to_full is None else round(p.hours_to_full, 2),
                    "hours_to_reset": round(p.hours_to_reset, 2), "exhausts_before_reset": p.exhausts_before_reset,
                    "label": p.label}
        return {"level": st.level, "pct": st.pct, "reset_at": st.reset_at,
                "burns": [{"t0": b.t0, "t1": b.t1, "delta_pct": b.delta_pct, "pts_per_hour": round(b.pts_per_hour, 3),
                           "label": "MEDIDO"} for b in active],
                "projection": proj}


def scan_leaks(ledger: LedgerService, thresholds: Any = None, since: str | None = None) -> dict[str, Any]:
    """F40: último reporte de cada desglose + quema de la ventana activa. `since` descarta reportes viejos."""
    from ..domain.budget import evaluate
    from ..domain.leaks import scan
    from ..domain.scoring import parse_iso

    snaps, _ = ledger.budget.snapshots()
    now = ledger.clock.now()
    st = evaluate(snaps, now)
    burns = [b for b in burn_rates(snaps) if b.reset_at == st.reset_at]
    proj = None
    if burns and st.pct is not None and st.reset_at:
        proj = project_exhaustion(st.pct, burns[-1].pts_per_hour, now, st.reset_at)
    reps = [r for r in ledger.latest().values() if since is None or parse_iso(r.captured_at) >= parse_iso(since)]
    findings = scan(reps, burns, proj, thresholds)
    return {"level": st.level, "pct": st.pct, "crit": sum(f.severity == "crit" for f in findings),
            "findings": [{"rule": f.rule, "severity": f.severity, "label": f.label, "message": f.message,
                          "evidence": f.evidence} for f in findings]}
