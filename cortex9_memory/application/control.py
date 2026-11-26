"""Casos de uso F42–F49: guard, contabilidad por tarea y mutex de trabajo pesado."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from ..domain.budget import evaluate
from ..domain.guard import ADMITIR, ESPERAR, GuardInput, Verdict, decide
from ..domain.lease import Lease, acquire, blocker, release
from ..domain.models import DomainError
from ..domain.ports import AppendLog, Clock, LeaseStore
from ..domain.scoring import parse_iso
from ..domain.splits import SplitPolicy, allocate, day_start_pct
from ..domain.tasks import TaskOutcome, TaskStart, close_task, spent_today
from ..domain.usage_ledger import burn_rates
from .budget import BudgetService
from .usage_ledger import LedgerService, scan_leaks


def _append(log: AppendLog, rec: dict[str, Any]) -> None:
    line = json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n"
    log.append_atomic(lambda cur: ("" if cur == "" or cur.endswith("\n") else "\n") + line)


class ControlService:
    def __init__(self, budget: BudgetService, ledger: LedgerService, tasks: AppendLog, lease: LeaseStore,
                 clock: Clock, policy: SplitPolicy | None = None) -> None:
        self.budget, self.ledger, self.tasks, self.lease, self.clock = budget, ledger, tasks, lease, clock
        self.policy = policy or SplitPolicy()

    # --- tareas ---
    def _records(self) -> tuple[dict[str, TaskStart], list[TaskOutcome]]:
        starts: dict[str, TaskStart] = {}
        ends: list[TaskOutcome] = []
        for ln in self.tasks.read_text().splitlines():
            try:
                d = json.loads(ln)
                ev = d.pop("event")
                if ev == "start":
                    starts[d["task_id"]] = TaskStart(**d)
                elif ev == "end":
                    d["warnings"] = tuple(d.get("warnings", ()))
                    ends.append(TaskOutcome(**d))
                    starts.pop(d["task_id"], None)
            except (json.JSONDecodeError, KeyError, TypeError, DomainError):
                continue
        return starts, ends

    # --- guard ---
    def guard(self, kind: str, agent: str, task_id: str, delta: float, needs: str | None = None,
              emergency: bool = False, ack: bool = False, take: bool = False, pct: float | None = None,
              reset_at: str | None = None) -> Verdict:
        now = self.clock.now()
        if pct is not None:
            if not reset_at:
                raise DomainError("--pct exige --reset-at")
            self.budget.record(pct, reset_at, agent, source="guard")
        snaps, _ = self.budget.snapshots()
        st = evaluate(snaps, now)
        window = [s for s in snaps if s.reset_at == st.reset_at]
        last_ts = max((s.ts for s in window), key=parse_iso, default=None)
        burns = [b for b in burn_rates(snaps) if b.reset_at == st.reset_at]
        burn = burns[-1].pts_per_hour if burns else None
        since = (parse_iso(now) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
        # burn_rate/exhaustion ya los cubren el cortacorrientes y los splits: solo cuentan hallazgos de reportes.
        crit = sum(1 for f in scan_leaks(self.ledger, since=since)["findings"]
                   if f["severity"] == "crit" and f["rule"] not in ("burn_rate", "exhaustion"))
        alloc = None
        _, ends = self._records()
        if st.pct is not None and st.reset_at:
            start = day_start_pct(snaps, now, st.reset_at, self.policy.tz_offset_min)
            spent = spent_today(ends, agent, now, st.reset_at, self.policy.tz_offset_min)
            alloc = allocate(self.policy, st.pct, start, now, st.reset_at, agent, spent)
        cur = Lease.from_json(self.lease.read())
        g = GuardInput(kind, agent, task_id, delta, now, st, last_ts, alloc, self.policy, burn, crit,
                       blocker(cur, agent, task_id, now), needs, emergency, ack)
        v = decide(g)
        if v.decision != ADMITIR or not take:
            return v
        if kind == "pesada":
            lost: list[Lease] = []

            def fn(text: str | None) -> str | None:
                got, b = acquire(Lease.from_json(text), agent, task_id, kind, now, self.policy.lease_ttl_min)
                if b:
                    lost.append(b)
                    return text
                return got.to_json() if got else text

            self.lease.update(fn)
            if lost:
                b = lost[0]
                return Verdict(ESPERAR, (f"pesado en vuelo: {b.task_id} de {b.agent} hasta {b.expires_at}",),
                               v.effort, v.checks)
        if st.pct is not None and st.reset_at:
            ts = TaskStart(task_id, agent, kind, delta, st.pct, now, st.reset_at)
            _append(self.tasks, {"event": "start", **ts.__dict__})
            self.budget.record(st.pct, st.reset_at, agent, task_id=task_id, phase="before", source="guard")
        return Verdict(v.decision, (*v.reasons, "lease y tarea registrados"), v.effort, v.checks)

    def task_end(self, task_id: str, agent: str, pct: float, reset_at: str | None = None,
                 tokens: int | None = None) -> TaskOutcome:
        starts, _ = self._records()
        start = starts.get(task_id)
        if start is None:
            raise DomainError(f"no hay tarea abierta {task_id!r} (abrirla con guard --acquire)")
        reset = reset_at or start.reset_at
        now = self.clock.now()
        self.budget.record(pct, reset, agent, task_id=task_id, phase="after", tokens_est=tokens, source="task-end")
        out = close_task(start, pct, now, reset, self.policy, tokens)
        _append(self.tasks, {"event": "end", **out.to_dict()})

        def drop_own(text: str | None) -> str | None:
            cur = Lease.from_json(text)
            return None if cur is not None and cur.owned_by(start.agent, task_id) else text

        self.lease.update(drop_own)
        return out

    def lease_status(self) -> dict[str, Any]:
        now = self.clock.now()
        cur = Lease.from_json(self.lease.read())
        return {"held": bool(cur and cur.active(now)), "lease": None if not cur else cur.__dict__,
                "open_tasks": sorted(self._records()[0])}

    def lease_release(self, agent: str, task_id: str, force: bool = False) -> dict[str, Any]:
        now = self.clock.now()
        res: list[tuple[bool, str]] = []

        def fn(text: str | None) -> str | None:
            ok, why = release(Lease.from_json(text), agent, task_id, now, force)
            res.append((ok, why))
            return None if ok else text

        self.lease.update(fn)
        return {"released": res[0][0], "reason": res[0][1]}
