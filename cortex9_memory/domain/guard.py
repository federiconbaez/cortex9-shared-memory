"""F45/F47/F48: veredicto ADMITIR / ESPERAR / DENEGAR antes de un bloque de trabajo. Puro."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .budget import BudgetStatus
from .lease import Lease
from .models import DomainError
from .scoring import parse_iso
from .splits import Allocation, SplitPolicy

ADMITIR, ESPERAR, DENEGAR = "ADMITIR", "ESPERAR", "DENEGAR"
EXIT = {ADMITIR: 0, ESPERAR: 10, DENEGAR: 11}
NEEDS = ("diseno", "depuracion", "revision")
KINDS = ("pesada", "liviana", "cierre")


@dataclass(frozen=True)
class GuardInput:
    kind: str
    agent: str
    task_id: str
    delta: float
    now: str
    status: BudgetStatus
    last_reading_ts: str | None
    alloc: Allocation | None
    policy: SplitPolicy
    burn_pph: float | None = None
    crit_findings: int = 0
    lease_blocker: Lease | None = None
    needs: str | None = None
    emergency: bool = False
    ack: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise DomainError(f"kind inválido {self.kind!r}; usar {KINDS}")
        if self.delta < 0:
            raise DomainError("delta >= 0")
        if self.needs is not None and self.needs not in NEEDS:
            raise DomainError(f"needs inválido {self.needs!r}; usar {NEEDS}")


@dataclass(frozen=True)
class Verdict:
    decision: str
    reasons: tuple[str, ...]
    effort: str
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return EXIT[self.decision]


def recommend_effort(level: str, needs: str | None) -> str:
    """I-EFFORT."""
    return "normal" if level == "VERDE" and needs in NEEDS else "low"


def decide(g: GuardInput) -> Verdict:
    """I-GUARD: DENEGAR > ESPERAR > ADMITIR; cierre nunca se deniega."""
    p, st = g.policy, g.status
    deny: list[str] = []
    wait: list[str] = []
    notes: list[str] = []
    effort = recommend_effort(st.level, g.needs)
    stale = g.last_reading_ts is None or (
        (parse_iso(g.now) - parse_iso(g.last_reading_ts)).total_seconds() / 3600.0 > p.stale_hours)
    checks: dict[str, Any] = {"level": st.level, "pct": st.pct, "stale_reading": stale, "burn_pph": g.burn_pph,
                              "crit_findings": g.crit_findings,
                              "alloc": g.alloc.to_dict() if g.alloc else None,
                              "lease_blocker": None if not g.lease_blocker else
                              {"agent": g.lease_blocker.agent, "task_id": g.lease_blocker.task_id,
                               "expires_at": g.lease_blocker.expires_at}}
    if g.kind == "cierre":
        if st.level == "ROJO":
            notes.append("ROJO: solo cierre; cerrar rápido")
        return Verdict(ADMITIR, tuple(notes) or ("cierre siempre admitido",), "low", checks)
    if st.pct is None or stale:
        (wait if g.kind == "pesada" else notes).append(
            f"sin lectura de uso vigente (o > {p.stale_hours:.0f} h): leer el reporte y `usage-ingest`")
    if st.level == "ROJO":
        deny.append("ROJO (≥ 90 %): solo tareas de cierre")
    if st.pct is not None and not g.emergency and st.pct + g.delta > p.cap:
        deny.append(f"tocaría la reserva: {st.pct:g} + {g.delta:g} > {p.cap:g} % (reserva {p.reserve_pct:g} %); "
                    "usar --emergency solo si es urgente")
    if g.kind == "pesada" and g.delta > p.max_task_pct:
        deny.append(f"tarea de {g.delta:g} pts > tope {p.max_task_pct:g}: partirla en bloques")
    if st.level == "AMBAR" and g.kind == "pesada" and not g.ack:
        wait.append("ÁMBAR (≥ 75 %): trabajo pesado solo con OK de Federico (--ack)")
    if g.burn_pph is not None:
        if g.burn_pph >= p.burn_crit:
            wait.append(f"cortacorrientes: quema {g.burn_pph:.1f} pts/h ≥ {p.burn_crit:g}; solo cierre")
        elif g.burn_pph >= p.burn_warn and g.kind == "pesada":
            wait.append(f"quema {g.burn_pph:.1f} pts/h ≥ {p.burn_warn:g}: solo trabajo liviano hasta enfriar")
    if g.crit_findings and g.kind == "pesada" and not g.ack:
        wait.append(f"{g.crit_findings} hallazgo(s) crítico(s) en leak-scan sin revisar (--ack con OK)")
    if g.alloc is not None and not g.emergency:
        if g.delta > g.alloc.day_left + 1e-9:
            wait.append(f"cuota del día agotada: quedan {max(0.0, g.alloc.day_left):.2f} de "
                        f"{g.alloc.day_budget:.2f} pts; seguir mañana")
        elif g.kind == "pesada" and g.delta > g.alloc.bot_left + 1e-9:
            wait.append(f"cuota de {g.agent} agotada: quedan {max(0.0, g.alloc.bot_left):.2f} de "
                        f"{g.alloc.bot_budget:.2f} pts; pedir split a Federico")
    if g.kind == "pesada" and g.lease_blocker is not None:
        b = g.lease_blocker
        wait.append(f"pesado en vuelo: {b.task_id} de {b.agent} hasta {b.expires_at}")
    if deny:
        return Verdict(DENEGAR, tuple(deny + wait), effort, checks)
    if wait:
        return Verdict(ESPERAR, tuple(wait), effort, checks)
    reason = f"admitida: {g.kind} {g.delta:g} pts, esfuerzo {effort}"
    return Verdict(ADMITIR, (reason, *notes), effort, checks)
