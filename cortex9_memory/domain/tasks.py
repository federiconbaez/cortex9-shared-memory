"""F46/F49: contabilidad por tarea y anomalías. Puro."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from .models import DomainError, require_slug
from .scoring import parse_iso
from .splits import SplitPolicy, local

KINDS = ("pesada", "liviana", "cierre")


@dataclass(frozen=True)
class TaskStart:
    task_id: str
    agent: str
    kind: str
    est_delta: float
    before_pct: float
    before_ts: str
    reset_at: str

    def __post_init__(self) -> None:
        require_slug(self.agent, "agent")
        if self.kind not in KINDS:
            raise DomainError(f"kind inválido {self.kind!r}")
        if self.est_delta < 0:
            raise DomainError("est_delta >= 0")
        parse_iso(self.before_ts)
        parse_iso(self.reset_at)


@dataclass(frozen=True)
class TaskOutcome:
    task_id: str
    agent: str
    kind: str
    est_delta: float
    delta: float
    after_pct: float
    after_ts: str
    reset_at: str
    anomaly: bool
    warnings: tuple[str, ...]
    tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["warnings"] = list(self.warnings)
        return d


def close_task(start: TaskStart, after_pct: float, after_ts: str, reset_at: str, policy: SplitPolicy,
               tokens: int | None = None) -> TaskOutcome:
    """I-TASK."""
    if parse_iso(after_ts) < parse_iso(start.before_ts):
        raise DomainError("after_ts anterior a before_ts")
    warnings: list[str] = []
    if reset_at != start.reset_at:
        warnings.append("la tarea cruzó un reset: delta no medible, se registra 0")
        delta = 0.0
    else:
        delta = round(after_pct - start.before_pct, 6)
        if delta < 0:
            warnings.append("lectura after menor que before: delta 0")
            delta = 0.0
    anomaly = delta > policy.max_task_pct or (
        start.est_delta > 0 and delta > policy.anomaly_ratio * start.est_delta) or (
        start.est_delta == 0 and delta > 0.5)
    if anomaly:
        warnings.append(f"anomalía: real {delta:g} pts vs estimado {start.est_delta:g}")
    return TaskOutcome(start.task_id, start.agent, start.kind, start.est_delta, delta, after_pct, after_ts, reset_at,
                       anomaly, tuple(warnings), tokens)


def spent_today(outcomes: Iterable[TaskOutcome], agent: str, now: str, reset_at: str, offset_min: int) -> float:
    today = local(now, offset_min).date()
    return round(sum(o.delta for o in outcomes if o.agent == agent and o.reset_at == reset_at
                     and local(o.after_ts, offset_min).date() == today), 6)
