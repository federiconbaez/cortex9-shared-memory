"""F31/F29: semáforo de consumo 50/75/90 % con histéresis, admisión y calibración. Puro."""
from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .models import DomainError, require_slug
from .scoring import parse_iso

THRESHOLDS = (("VERDE", 0.0), ("AMARILLO", 50.0), ("AMBAR", 75.0), ("ROJO", 90.0))
POLICY: dict[str, dict[str, Any]] = {
    "VERDE": {"pack_budget": 800, "max_parallel_handoffs": 3, "heavy": True, "summary_every_turns": 12},
    "AMARILLO": {"pack_budget": 450, "max_parallel_handoffs": 1, "heavy": True, "summary_every_turns": 6},
    "AMBAR": {"pack_budget": 300, "max_parallel_handoffs": 0, "heavy": False, "summary_every_turns": 4},
    "ROJO": {"pack_budget": 200, "max_parallel_handoffs": 0, "heavy": False, "summary_every_turns": 2},
    "DESCONOCIDO": {"pack_budget": 450, "max_parallel_handoffs": 1, "heavy": False, "summary_every_turns": 6},
}
# Techo que una tarea no puede cruzar según su tipo.
CEILING = {"pesada": 75.0, "liviana": 90.0, "cierre": 100.0}
PHASES = ("before", "after", "point")


def level_for(pct: float) -> str:
    level = "VERDE"
    for name, lo in THRESHOLDS:
        if pct >= lo:
            level = name
    return level


@dataclass(frozen=True)
class UsageSnapshot:
    ts: str
    pct: float
    reset_at: str
    agent: str
    source: str = "platform"
    task_id: str | None = None
    phase: str = "point"
    tokens_est: int | None = None

    def __post_init__(self) -> None:
        for v in (self.ts, self.reset_at):
            try:
                parse_iso(v)
            except ValueError as exc:
                raise DomainError(f"fecha ISO-8601 inválida: {v!r}") from exc
        if not 0.0 <= float(self.pct) <= 100.0:
            raise DomainError("pct debe estar en [0, 100]")
        require_slug(self.agent, "agent")
        if self.phase not in PHASES:
            raise DomainError(f"phase inválida {self.phase!r}; usar {PHASES}")
        if self.tokens_est is not None and self.tokens_est < 0:
            raise DomainError("tokens_est >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("ts", "pct", "reset_at", "agent", "source", "task_id", "phase",
                                               "tokens_est")}


@dataclass(frozen=True)
class BudgetStatus:
    level: str
    pct: float | None
    reset_at: str | None
    policy: dict[str, Any] = field(default_factory=dict)
    alert: str | None = None
    readings: int = 0


def _alert(level: str, pct: float, reset_at: str) -> str | None:
    if level == "VERDE":
        return None
    msg = {
        "AMARILLO": "modo económico: context-pack 450, un handoff a la vez, sin tareas especulativas",
        "AMBAR": "freno operativo (umbral 75): sin trabajo pesado ni handoffs autónomos; seguir solo con OK",
        "ROJO": "circuit breaker (umbral 90): solo cierre de cápsulas y backup hasta el reset",
    }[level]
    return f"Uso semanal en {pct:.1f}% ({level}): {msg}. Reset {reset_at} (UTC)."


def evaluate(snapshots: Iterable[UsageSnapshot], now: str) -> BudgetStatus:
    """I-BUDGET: toma la ventana activa (reset_at futuro más próximo) y el máximo pct leído en ella."""
    t = parse_iso(now)
    active = [s for s in snapshots if parse_iso(s.reset_at) > t]
    if not active:
        return BudgetStatus("DESCONOCIDO", None, None, dict(POLICY["DESCONOCIDO"]),
                            "Sin lectura de uso en la ventana actual: consultar el reporte antes de trabajo pesado.")
    reset = min(active, key=lambda s: parse_iso(s.reset_at)).reset_at
    window = [s for s in active if s.reset_at == reset]
    pct = max(float(s.pct) for s in window)
    level = level_for(pct)
    return BudgetStatus(level, pct, reset, dict(POLICY[level]), _alert(level, pct, reset), len(window))


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str
    headroom_pct: float | None


def admit(status: BudgetStatus, kind: str, est_delta_pct: float) -> Admission:
    if kind not in CEILING:
        raise DomainError(f"tipo de tarea inválido {kind!r}; usar {tuple(CEILING)}")
    if est_delta_pct < 0:
        raise DomainError("est_delta_pct >= 0")
    if status.pct is None:
        ok = kind != "pesada"
        return Admission(ok, "sin lectura vigente: " + ("solo trabajo liviano" if ok else "leer uso antes"), None)
    if status.level == "ROJO" and kind != "cierre":
        return Admission(False, "ROJO: solo tareas de cierre", 100.0 - status.pct)
    if status.level == "AMBAR" and kind == "pesada":
        return Admission(False, "AMBAR: sin trabajo pesado", CEILING["liviana"] - status.pct)
    ceiling = CEILING[kind]
    headroom = round(ceiling - status.pct, 3)
    projected = status.pct + est_delta_pct
    crosses = projected > 100.0 if ceiling >= 100.0 else projected >= ceiling
    if crosses:
        return Admission(False, f"cruzaría el techo de {ceiling:.0f}% para tarea {kind}", headroom)
    return Admission(True, f"admitida: margen {headroom:.1f} puntos hasta {ceiling:.0f}%", headroom)


def calibrate(snapshots: Iterable[UsageSnapshot]) -> float | None:
    """F29: mediana de puntos porcentuales por cada 1000 tokens estimados, por par before/after de una tarea."""
    before: dict[str, float] = {}
    rates: list[float] = []
    for s in sorted(snapshots, key=lambda x: parse_iso(x.ts)):
        if not s.task_id:
            continue
        if s.phase == "before":
            before[s.task_id] = s.pct
        elif s.phase == "after" and s.task_id in before and s.tokens_est:
            delta = s.pct - before.pop(s.task_id)
            if delta >= 0:
                rates.append(delta / s.tokens_est * 1000.0)
    return statistics.median(rates) if rates else None


def predict_delta(rate_per_k: float | None, tokens_est: int) -> float | None:
    return None if rate_per_k is None else rate_per_k * tokens_est / 1000.0
