"""Casos de uso del log de eventos y del ledger de decisiones (F25, F21)."""
from __future__ import annotations

import json
from typing import Any

from ..domain.events import build_next, fold_ledger, render_ledger, verify
from ..domain.ports import AppendLog, Clock


class EventService:
    def __init__(self, log: AppendLog, clock: Clock) -> None:
        self.log, self.clock = log, clock

    def append(self, agent: str, kind: str, task_id: str | None = None, payload: dict[str, Any] | None = None,
               payload_ref: str | None = None) -> dict[str, Any]:
        ts = self.clock.now()
        line = self.log.append_atomic(lambda cur: build_next(cur, ts=ts, agent=agent, kind=kind, task_id=task_id,
                                                             payload=payload, payload_ref=payload_ref))
        return dict(json.loads(line))

    def verify(self, repair: bool = False) -> dict[str, Any]:
        rep = verify(self.log.read_text())
        repaired = False
        if repair and rep.torn_offset is not None and rep.broken_at is None:
            self.log.truncate_to(rep.torn_offset)
            repaired = True
        return {"ok": rep.ok or repaired, "valid": rep.valid, "last_sha256": rep.last_sha256,
                "torn_offset": rep.torn_offset, "broken_at": rep.broken_at, "merkle_root": rep.merkle_root,
                "repaired": repaired}

    def decide(self, agent: str, did: str, statement: str, status: str = "vigente",
               supersedes: str | None = None, task_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": did, "statement": statement, "status": status}
        if supersedes:
            payload["supersedes"] = supersedes
        return self.append(agent, "decision", task_id, payload)

    def contradict(self, agent: str, cid: str, statement: str, status: str = "abierta",
                   resolved_by: str | None = None, task_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": cid, "statement": statement, "status": status}
        if resolved_by:
            payload["resolved_by"] = resolved_by
        return self.append(agent, "contradiction", task_id, payload)

    def ledger(self) -> str:
        return render_ledger(fold_ledger(self.log.read_text()))
