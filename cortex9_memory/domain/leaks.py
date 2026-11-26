"""F40/F41: reglas de fuga de consumo y lint de mensajes entre bots. Puro."""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .models import DomainError
from .scoring import estimate_tokens
from .usage_ledger import Burn, Projection, UsageReport

SEVERITY_ORDER = ("crit", "warn", "info")
LABELS = ("MEDIDO", "ESTIMADO")
LIMITS = {"handoff": 280, "brief": 700, "result": 350, "user": 250}
MAX_PASTED_LINES = 20
MAX_LOG_LINES = 15
MAX_REPEATS = 3
_B2B = re.compile(r"^bot[- ]to[- ]bot$", re.I)
_DELETED = re.compile(r"delet|borrad|eliminad", re.I)
_NOT_ROUTINE = re.compile(r"^not from a routine$", re.I)
_DAY = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})\b", re.I)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_DAYS_IN = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_LOGLINE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}|\[\d{2}:\d{2}|\$ |>>> |Traceback|  File \"|"
                      r"(INFO|WARN|WARNING|ERROR|DEBUG)\b)|\b(PASSED|FAILED|ok \d+|not ok)\b")


@dataclass(frozen=True)
class Thresholds:
    b2b_warn: float = 10.0
    b2b_crit: float = 25.0
    b2b_ratio_warn: float = 0.35
    day_warn: float = 25.0
    day_crit: float = 40.0
    burst_two_days: float = 50.0
    burn_warn: float = 4.0
    burn_crit: float = 7.0
    routine_per_run_warn: float = 1.0


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    label: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            raise DomainError(f"severidad inválida {self.severity!r}")
        if self.label not in LABELS:
            raise DomainError(f"etiqueta inválida {self.label!r}")


def _sev(value: float, warn: float, crit: float) -> str | None:
    return "crit" if value >= crit else "warn" if value >= warn else None


def _day_ordinal(name: str) -> int | None:
    m = _DAY.search(name)
    if not m:
        return None
    mi = _MONTHS.index(m.group(1)[:3].lower())
    return sum(_DAYS_IN[:mi]) + int(m.group(2))


def _scan_bot_activity(rep: UsageReport, th: Thresholds) -> list[Finding]:
    out: list[Finding] = []
    total = 0.0
    if rep.dims[:1] == ("bot",):
        for bot in rep.top():
            b2b = sum(c.share_pct for c in rep.children(bot.name) if _B2B.match(c.name))
            total += b2b
            if bot.share_pct > 0 and b2b / bot.share_pct >= th.b2b_ratio_warn and b2b >= th.b2b_warn / 2:
                out.append(Finding("bot_to_bot_ratio", "warn", "MEDIDO",
                                   f"{bot.name}: {b2b:g}% de {bot.share_pct:g}% es bot a bot; handoffs verbosos",
                                   {"bot": bot.name, "b2b_pct": b2b, "bot_pct": bot.share_pct,
                                    "period": rep.period}))
            if _DELETED.search(bot.name) and bot.share_pct > 0:
                out.append(Finding("deleted_bots", "warn", "MEDIDO",
                                   f"bots borrados siguen consumiendo {bot.share_pct:g}%",
                                   {"share_pct": bot.share_pct, "period": rep.period}))
    elif rep.dims[:1] == ("activity",):
        total = sum(r.share_pct for r in rep.top() if _B2B.match(r.name))
    sev = _sev(total, th.b2b_warn, th.b2b_crit)
    if sev:
        out.append(Finding("bot_to_bot_total", sev, "MEDIDO",
                           f"bot a bot = {total:g}% del uso ({rep.period}); usar handoff por punteros",
                           {"share_pct": total, "period": rep.period}))
    return out


def _scan_days(rep: UsageReport, th: Thresholds) -> list[Finding]:
    out: list[Finding] = []
    days = rep.top()
    for d in days:
        sev = _sev(d.share_pct, th.day_warn, th.day_crit)
        if sev:
            out.append(Finding("day_peak", sev, "MEDIDO", f"{d.name}: {d.share_pct:g}% del período en un día",
                               {"day": d.name, "share_pct": d.share_pct, "period": rep.period}))
    for a, b in zip(days, days[1:], strict=False):
        oa, ob = _day_ordinal(a.name), _day_ordinal(b.name)
        consecutive = oa is not None and ob is not None and (ob - oa == 1 or (oa == sum(_DAYS_IN) and ob == 1))
        if consecutive and a.share_pct + b.share_pct >= th.burst_two_days:
            s = a.share_pct + b.share_pct
            out.append(Finding("burst_two_days", "crit", "MEDIDO",
                               f"ráfaga: {a.name} + {b.name} = {s:g}% del período",
                               {"days": [a.name, b.name], "share_pct": s, "period": rep.period}))
    return out


def _scan_routines(rep: UsageReport, th: Thresholds) -> list[Finding]:
    out: list[Finding] = []
    for r in rep.top():
        if _NOT_ROUTINE.match(r.name) or r.paused or not r.runs_per_week:
            continue
        per_run = r.share_pct / r.runs_per_week
        if per_run >= th.routine_per_run_warn:
            out.append(Finding("routine_per_run", "warn", "ESTIMADO",
                               f"routine '{r.name}': ~{per_run:.2f}% por corrida; ¿corre sin cambios?",
                               {"routine": r.name, "share_pct": r.share_pct, "runs_per_week": r.runs_per_week,
                                "per_run_pct": round(per_run, 3)}))
    return out


def scan(reports: Iterable[UsageReport], burns: Sequence[Burn] = (), projection: Projection | None = None,
         thresholds: Thresholds | None = None) -> list[Finding]:
    """I-LEAK: reglas deterministas; MEDIDO solo con números registrados."""
    th = thresholds or Thresholds()
    out: list[Finding] = []
    for rep in reports:
        first = rep.dims[:1]
        if first in (("bot",), ("activity",)):
            out += _scan_bot_activity(rep, th)
        elif first == ("day",):
            out += _scan_days(rep, th)
        elif first == ("routine",):
            out += _scan_routines(rep, th)
    if burns:
        last = burns[-1]
        sev = _sev(last.pts_per_hour, th.burn_warn, th.burn_crit)
        if sev:
            out.append(Finding("burn_rate", sev, "MEDIDO",
                               f"quema {last.pts_per_hour:.1f} pts/h ({last.pct0:g}%→{last.pct1:g}%)",
                               {"t0": last.t0, "t1": last.t1, "pts_per_hour": round(last.pts_per_hour, 3)}))
    if projection and projection.exhausts_before_reset:
        out.append(Finding("exhaustion", "crit", "ESTIMADO",
                           f"al ritmo actual el cupo se agota en {projection.hours_to_full or 0:.1f} h, "
                           f"antes del reset ({projection.hours_to_reset:.1f} h)",
                           {"hours_to_full": projection.hours_to_full, "hours_to_reset": projection.hours_to_reset}))
    return sorted(out, key=lambda f: SEVERITY_ORDER.index(f.severity))


@dataclass(frozen=True)
class LintResult:
    ok: bool
    kind: str
    tokens: int
    limit: int
    issues: tuple[str, ...]
    suggestions: tuple[str, ...]


def _fenced_blocks(lines: list[str]) -> list[int]:
    sizes, inside, n = [], False, 0
    for ln in lines:
        if ln.strip().startswith("```"):
            if inside:
                sizes.append(n)
            inside, n = not inside, 0
        elif inside:
            n += 1
    if inside:
        sizes.append(n)
    return sizes


def lint_message(text: str, kind: str) -> LintResult:
    """I-LINT: tope de tokens por tipo y sin contenido pegado."""
    if kind not in LIMITS:
        raise DomainError(f"tipo inválido {kind!r}; usar {tuple(LIMITS)}")
    limit = LIMITS[kind]
    tokens = estimate_tokens(text) if text else 0
    lines = text.splitlines()
    issues: list[str] = []
    tips: list[str] = []
    if tokens > limit:
        issues.append(f"{tokens} tokens > tope {limit} para '{kind}'")
        tips.append("dejar solo decisión, estado y próximos pasos; el resto como punteros path#Lx-Ly@sha256")
    big = [n for n in _fenced_blocks(lines) if n > MAX_PASTED_LINES]
    if big:
        issues.append(f"bloque pegado de {max(big)} líneas (> {MAX_PASTED_LINES})")
        tips.append("guardar el bloque en un archivo y pasar el puntero, o `c9mem fold`")
    logs = sum(1 for ln in lines if _LOGLINE.search(ln))
    if logs > MAX_LOG_LINES:
        issues.append(f"{logs} líneas de log pegadas (> {MAX_LOG_LINES})")
        tips.append("`c9mem fold` deja cabeza, fallas únicas, resumen y cola")
    counts: dict[str, int] = {}
    for ln in lines:
        k = ln.strip().lower()
        if k:
            counts[k] = counts.get(k, 0) + 1
    rep = max(counts.values(), default=0)
    if rep > MAX_REPEATS:
        issues.append(f"renglón repetido {rep} veces")
        tips.append("deduplicar; decir 'N veces' una sola vez")
    if kind == "handoff" and issues:
        tips.append("armar el sobre con `c9mem handoff-prepare`")
    return LintResult(not issues, kind, tokens, limit, tuple(issues), tuple(dict.fromkeys(tips)))
