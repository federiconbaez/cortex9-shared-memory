"""F39: ledger de shares de uso por bot/actividad/día/routine y velocidad de quema. Puro."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

from .budget import UsageSnapshot
from .models import DomainError
from .scoring import parse_iso

APPROX_LT1 = 0.5
DIMENSIONS = ("bot", "activity", "routine", "day")
_PCT = re.compile(r"Weekly usage limit:\s*(\d+(?:\.\d+)?)%\s*used", re.I)
_HEADER = re.compile(
    r"usage\s+(over the last 7 days|over the last 30 days|this week|this billing cycle)?[^\n]*?,\s*by\s+"
    r"([a-z ,]+?),\s*as shares",
    re.I,
)
_ROW = re.compile(r"^(?P<indent>\s*)-\s+(?P<name>.+):\s+(?P<pct><1|\d+(?:\.\d+)?)%(?P<rest>.*)$")
_RUNS = re.compile(r"about\s+(\d+)\s+runs?\s+a\s+week", re.I)
_PERIODS = {
    "over the last 7 days": "last_7_days",
    "over the last 30 days": "last_30_days",
    "this week": "this_week",
    "this billing cycle": "this_billing_cycle",
}


@dataclass(frozen=True)
class ShareRow:
    name: str
    share_pct: float
    level: int = 0
    parent: str | None = None
    approx: bool = False
    runs_per_week: int | None = None
    paused: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.share_pct <= 100.0:
            raise DomainError("share_pct debe estar en [0, 100]")
        if self.level > 0 and not self.parent:
            raise DomainError(f"fila hija sin padre: {self.name!r}")


@dataclass(frozen=True)
class UsageReport:
    captured_at: str
    pct_used: float | None
    period: str | None
    dims: tuple[str, ...]
    rows: tuple[ShareRow, ...]

    @property
    def key(self) -> str:
        return f"{self.period or '?'}:{'>'.join(self.dims) or '-'}"

    def top(self) -> list[ShareRow]:
        return [r for r in self.rows if r.level == 0]

    def children(self, parent: str) -> list[ShareRow]:
        return [r for r in self.rows if r.parent == parent]

    def to_dict(self) -> dict[str, Any]:
        return {"captured_at": self.captured_at, "pct_used": self.pct_used, "period": self.period,
                "dims": list(self.dims), "rows": [asdict(r) for r in self.rows]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UsageReport:
        return cls(d["captured_at"], d.get("pct_used"), d.get("period"), tuple(d.get("dims", ())),
                   tuple(ShareRow(**r) for r in d.get("rows", ())))


def _dims(text: str) -> tuple[str, ...]:
    words = re.split(r"\s*(?:,|\band\b)\s*", text.strip().lower())
    out = tuple(w for w in words if w)
    bad = [w for w in out if w not in DIMENSIONS]
    if bad:
        raise DomainError(f"dimensión desconocida {bad!r}")
    return out


def parse_report(text: str, captured_at: str) -> UsageReport:
    """I-LEDGER: solo filas `- nombre: N%` reales; nada se normaliza ni se completa."""
    parse_iso(captured_at)
    m = _PCT.search(text)
    pct = float(m.group(1)) if m else None
    h = _HEADER.search(text)
    period = _PERIODS.get(h.group(1).lower()) if h and h.group(1) else None
    dims = _dims(h.group(2)) if h else ()
    rows: list[ShareRow] = []
    stack: list[str] = []
    for line in text.splitlines():
        r = _ROW.match(line.rstrip())
        if not r:
            continue
        level = len(r.group("indent").expandtabs(2)) // 2
        raw = r.group("pct")
        approx = raw == "<1"
        share = APPROX_LT1 if approx else float(raw)
        parent = stack[level - 1] if 0 < level <= len(stack) else None
        if level > 0 and parent is None:
            raise DomainError(f"fila hija sin padre: {line.strip()!r}")
        rest = r.group("rest")
        runs = _RUNS.search(rest)
        name = r.group("name").strip()
        rows.append(ShareRow(name, share, level, parent, approx, int(runs.group(1)) if runs else None,
                             "paused" in rest.lower()))
        stack = stack[:level] + [name]
    if not rows and pct is None:
        raise DomainError("el texto no trae filas de share ni 'Weekly usage limit'")
    return UsageReport(captured_at, pct, period, dims, tuple(rows))


@dataclass(frozen=True)
class Burn:
    reset_at: str
    t0: str
    t1: str
    pct0: float
    pct1: float

    @property
    def delta_pct(self) -> float:
        return round(self.pct1 - self.pct0, 6)

    @property
    def hours(self) -> float:
        return (parse_iso(self.t1) - parse_iso(self.t0)).total_seconds() / 3600.0

    @property
    def pts_per_hour(self) -> float:
        return self.delta_pct / self.hours


def burn_rates(snapshots: Iterable[UsageSnapshot]) -> list[Burn]:
    """I-BURN: pares consecutivos de la misma ventana, Δt > 0 y Δpct ≥ 0."""
    by_window: dict[str, list[UsageSnapshot]] = {}
    for s in snapshots:
        by_window.setdefault(s.reset_at, []).append(s)
    out: list[Burn] = []
    for reset, snaps in by_window.items():
        ordered = sorted(snaps, key=lambda x: parse_iso(x.ts))
        for a, b in zip(ordered, ordered[1:], strict=False):
            if parse_iso(b.ts) > parse_iso(a.ts) and b.pct >= a.pct:
                out.append(Burn(reset, a.ts, b.ts, float(a.pct), float(b.pct)))
    return sorted(out, key=lambda x: parse_iso(x.t1))


@dataclass(frozen=True)
class Projection:
    pct: float
    pts_per_hour: float
    hours_to_full: float | None
    hours_to_reset: float
    exhausts_before_reset: bool
    label: str = "ESTIMADO"


def project_exhaustion(pct: float, pts_per_hour: float, now: str, reset_at: str) -> Projection:
    to_reset = (parse_iso(reset_at) - parse_iso(now)).total_seconds() / 3600.0
    if pts_per_hour <= 0:
        return Projection(pct, pts_per_hour, None, to_reset, False)
    to_full = (100.0 - pct) / pts_per_hour
    exhausts = parse_iso(now) + timedelta(hours=to_full) < parse_iso(reset_at)
    return Projection(pct, pts_per_hour, to_full, to_reset, exhausts)
