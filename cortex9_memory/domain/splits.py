"""F42/F43: presupuesto por split semana → día → bot → tarea con reserva intocable. Puro."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from typing import Any

from .budget import UsageSnapshot
from .models import DomainError
from .scoring import parse_iso

DEFAULT_WEIGHTS = {"ben": 0.6, "tween-forge": 0.3, "federica-fondos": 0.1}


@dataclass(frozen=True)
class SplitPolicy:
    reserve_pct: float = 15.0
    weights: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    default_weight: float = 0.1
    max_task_pct: float = 8.0
    tz_offset_min: int = -180
    stale_hours: float = 2.0
    burn_warn: float = 4.0
    burn_crit: float = 7.0
    lease_ttl_min: int = 90
    anomaly_ratio: float = 2.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.reserve_pct < 100.0:
            raise DomainError("reserve_pct debe estar en [0, 100)")
        if any(w < 0 for w in self.weights.values()) or self.default_weight < 0:
            raise DomainError("pesos >= 0")
        if self.max_task_pct <= 0 or self.lease_ttl_min <= 0:
            raise DomainError("max_task_pct y lease_ttl_min > 0")

    @property
    def cap(self) -> float:
        return 100.0 - self.reserve_pct

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> SplitPolicy:
        known = {f.name for f in fields(cls)}
        bad = set(d) - known
        if bad:
            raise DomainError(f"claves de política desconocidas: {sorted(bad)}")
        return cls(**dict(d))

    def share(self, agent: str) -> float:
        w = self.weights.get(agent, self.default_weight)
        total = sum(self.weights.values()) + (0.0 if agent in self.weights else self.default_weight)
        return 0.0 if total <= 0 else w / total


def local(ts: str, offset_min: int) -> datetime:
    return parse_iso(ts) + timedelta(minutes=offset_min)


def days_left(now: str, reset_at: str, offset_min: int) -> int:
    return max(1, (local(reset_at, offset_min).date() - local(now, offset_min).date()).days)


def day_start_pct(snapshots: Iterable[UsageSnapshot], now: str, reset_at: str, offset_min: int) -> float | None:
    """Máximo leído antes del inicio del día local en la ventana; si no hay, el mínimo de hoy."""
    today = local(now, offset_min).date()
    window = [s for s in snapshots if s.reset_at == reset_at and parse_iso(s.ts) <= parse_iso(now)]
    before = [s.pct for s in window if local(s.ts, offset_min).date() < today]
    if before:
        return max(before)
    todays = [s.pct for s in window]
    return min(todays) if todays else None


@dataclass(frozen=True)
class Allocation:
    cap: float
    reserve_pct: float
    pct: float
    days_left: int
    day_budget: float
    day_spent: float
    day_left: float
    bot_share: float
    bot_budget: float
    bot_spent: float
    bot_left: float
    task_cap: float
    reserve_headroom: float

    def to_dict(self) -> dict[str, Any]:
        return {k.name: (round(v, 3) if isinstance(v, float) else v)
                for k, v in ((f, getattr(self, f.name)) for f in fields(self))}


def allocate(policy: SplitPolicy, pct: float, pct_day_start: float | None, now: str, reset_at: str, agent: str,
             bot_spent_today: float = 0.0) -> Allocation:
    """I-SPLIT."""
    start = pct if pct_day_start is None else min(pct_day_start, pct)
    dl = days_left(now, reset_at, policy.tz_offset_min)
    day_budget = max(0.0, (policy.cap - start) / dl)
    day_spent = max(0.0, pct - start)
    share = policy.share(agent)
    bot_budget = day_budget * share
    bot_left = bot_budget - max(0.0, bot_spent_today)
    return Allocation(policy.cap, policy.reserve_pct, pct, dl, day_budget, day_spent, day_budget - day_spent, share,
                      bot_budget, max(0.0, bot_spent_today), bot_left, max(0.0, min(policy.max_task_pct, bot_left)),
                      policy.cap - pct)
