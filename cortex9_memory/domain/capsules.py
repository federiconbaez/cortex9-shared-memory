"""F08/F22/F26: cápsulas por tarea (<= 1 página), CAS sobre rev, reloj vectorial y sello. Puro."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .models import DomainError, require_slug
from .scoring import estimate_tokens, parse_iso
from .taint import TrustTier, assess

STATUSES = ("propuesta", "en_progreso", "bloqueado", "listo", "cerrado")
MAX_DECISIONES, MAX_PROXIMOS, MAX_PUNTEROS, MAX_ESTADO_LINES = 5, 3, 8, 3
DEFAULT_BUDGET = 450
FORMAT_VERSION = 1
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA = re.compile(r"^[0-9a-f]{12,64}$")
_LINES = re.compile(r"^L\d+(?:-L\d+)?$")
_LIST_FIELDS = ("decisiones", "proximos", "punteros", "collaborators")
_SET_FIELDS = ("estado", "status", "owner", "ttl_hours")
_SECTIONS = (("estado", "## Estado"), ("decisiones", "## Decisiones"), ("proximos", "## Proximos pasos"),
             ("punteros", "## Punteros"))


class ConflictError(DomainError):
    """I-CAS: la revisión esperada no coincide con la actual."""


@dataclass(frozen=True)
class Pointer:
    """Referencia sin pérdida a contenido en disco: path#Lx-Ly@sha256."""

    path: str
    sha256: str | None = None
    lines: str | None = None

    def __post_init__(self) -> None:
        if not self.path.startswith("/") or any(ch in self.path for ch in "#@\n"):
            raise DomainError(f"puntero debe ser ruta absoluta sin #/@: {self.path!r}")
        if self.sha256 is not None and not _SHA.match(self.sha256):
            raise DomainError(f"sha256 inválido en puntero: {self.sha256!r}")
        if self.lines is not None and not _LINES.match(self.lines):
            raise DomainError(f"rango de líneas inválido (Lx o Lx-Ly): {self.lines!r}")

    def ref(self) -> str:
        out = self.path
        if self.lines:
            out += f"#{self.lines}"
        if self.sha256:
            out += f"@{self.sha256}"
        return out

    @staticmethod
    def parse(ref: str) -> Pointer:
        rest, _, sha = ref.strip().partition("@")
        path, _, lines = rest.partition("#")
        return Pointer(path, sha or None, lines or None)


def _single_line(items: Iterable[str], what: str) -> tuple[str, ...]:
    out = []
    for it in items:
        s = str(it).strip()
        if not s or "\n" in s:
            raise DomainError(f"{what}: cada ítem es una línea no vacía")
        out.append(s)
    return tuple(out)


@dataclass(frozen=True)
class Capsule:
    task_id: str
    owner: str
    estado: str
    status: str = "en_progreso"
    decisiones: tuple[str, ...] = ()
    proximos: tuple[str, ...] = ()
    punteros: tuple[Pointer, ...] = ()
    collaborators: tuple[str, ...] = ()
    rev: int = 1
    updated_at: str = ""
    ttl_hours: int = 48
    clock: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    budget_tokens: int = DEFAULT_BUDGET

    def __post_init__(self) -> None:
        if not _TASK_ID.match(self.task_id):
            raise DomainError(f"task_id inválido {self.task_id!r}")
        require_slug(self.owner, "owner")
        for c in self.collaborators:
            require_slug(c, "collaborator")
        if self.status not in STATUSES:
            raise DomainError(f"status inválido {self.status!r}; usar {STATUSES}")
        estado = self.estado.strip()
        lines = estado.splitlines()
        if not estado or len(lines) > MAX_ESTADO_LINES or any(ln.lstrip().startswith("#") for ln in lines):
            raise DomainError(f"estado: 1..{MAX_ESTADO_LINES} líneas, sin encabezados")
        object.__setattr__(self, "estado", estado)
        object.__setattr__(self, "decisiones", _single_line(self.decisiones, "decisiones"))
        object.__setattr__(self, "proximos", _single_line(self.proximos, "proximos"))
        if len(self.decisiones) > MAX_DECISIONES or len(self.proximos) > MAX_PROXIMOS:
            raise DomainError(f"máximo {MAX_DECISIONES} decisiones y {MAX_PROXIMOS} próximos pasos")
        if len(self.punteros) > MAX_PUNTEROS:
            raise DomainError(f"máximo {MAX_PUNTEROS} punteros")
        if self.rev < 1 or self.ttl_hours < 1 or self.budget_tokens < 100:
            raise DomainError("rev >= 1, ttl_hours >= 1, budget_tokens >= 100")
        object.__setattr__(self, "clock", tuple(sorted((require_slug(a, "clock"), int(n)) for a, n in self.clock)))
        for text in (self.estado, *self.decisiones, *self.proximos):
            v = assess(text, TrustTier.T1)
            if v.blocked:
                raise DomainError(f"cápsula bloqueada por taint-guard: {', '.join(v.reasons)}")
        used = estimate_tokens(render(self))
        if used > self.budget_tokens:
            raise DomainError(f"cápsula de {used} tokens supera el presupuesto de {self.budget_tokens} tokens")

    def meta(self) -> dict[str, Any]:
        return {
            "v": FORMAT_VERSION,
            "task_id": self.task_id,
            "owner": self.owner,
            "status": self.status,
            "rev": self.rev,
            "updated_at": self.updated_at,
            "ttl_hours": self.ttl_hours,
            "collaborators": list(self.collaborators),
            "clock": dict(self.clock),
            "budget_tokens": self.budget_tokens,
        }

    def seal(self) -> str:
        """F26: sha256 del contenido canónico completo."""
        body = dict(self.meta(), estado=self.estado, decisiones=list(self.decisiones), proximos=list(self.proximos),
                    punteros=[p.ref() for p in self.punteros])
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def render(c: Capsule) -> str:
    meta = dict(c.meta(), seal=c.seal())
    out = ["---", json.dumps(meta, sort_keys=True, ensure_ascii=False, separators=(",", ":")), "---",
           f"# {c.task_id} · {c.status} · rev {c.rev}"]
    out += ["## Estado", c.estado]
    out += ["## Decisiones", *[f"- {d}" for d in c.decisiones]]
    out += ["## Proximos pasos", *[f"- {p}" for p in c.proximos]]
    out += ["## Punteros", *[f"- {p.ref()}" for p in c.punteros]]
    return "\n".join(out) + "\n"


def parse(text: str) -> Capsule:
    lines = text.split("\n")
    if len(lines) < 4 or lines[0] != "---" or lines[2] != "---" or not text.endswith("\n"):
        raise DomainError("cápsula truncada o sin front matter")
    try:
        meta = json.loads(lines[1])
    except json.JSONDecodeError as exc:
        raise DomainError(f"front matter inválido: {exc}") from exc
    if meta.get("v") != FORMAT_VERSION:
        raise DomainError(f"versión de cápsula desconocida: {meta.get('v')!r}")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    headers = {h: k for k, h in _SECTIONS}
    for ln in lines[4:]:
        if ln in headers:
            current = headers[ln]
            sections[current] = []
        elif current is not None and ln != "":
            sections[current].append(ln)
    if set(sections) != {k for k, _ in _SECTIONS}:
        raise DomainError("cápsula truncada: faltan secciones")

    def bullets(key: str) -> list[str]:
        items = sections[key]
        if any(not i.startswith("- ") for i in items):
            raise DomainError(f"sección {key} mal formada")
        return [i[2:] for i in items]

    try:
        cap = Capsule(
            task_id=meta["task_id"], owner=meta["owner"], estado="\n".join(sections["estado"]),
            status=meta["status"], decisiones=tuple(bullets("decisiones")), proximos=tuple(bullets("proximos")),
            punteros=tuple(Pointer.parse(p) for p in bullets("punteros")),
            collaborators=tuple(meta.get("collaborators", [])), rev=int(meta["rev"]), updated_at=meta["updated_at"],
            ttl_hours=int(meta["ttl_hours"]), clock=tuple(meta.get("clock", {}).items()),
            budget_tokens=int(meta["budget_tokens"]),
        )
    except KeyError as exc:
        raise DomainError(f"front matter incompleto: falta {exc}") from exc
    if cap.seal() != meta.get("seal"):
        raise DomainError("sello inválido: la cápsula fue editada fuera de protocolo o está truncada")
    return cap


def create(task_id: str, owner: str, estado: str, now: str, agent: str, **kw: Any) -> Capsule:
    kw.setdefault("punteros", ())
    kw["punteros"] = tuple(kw["punteros"])
    for key in ("decisiones", "proximos", "collaborators"):
        if key in kw:
            kw[key] = tuple(kw[key])
    return Capsule(task_id=task_id, owner=owner, estado=estado, rev=1, updated_at=now,
                   clock=((require_slug(agent, "agent"), 1),), **kw)


def _as_pointer(v: Any) -> Pointer:
    return v if isinstance(v, Pointer) else Pointer.parse(str(v))


def apply_patch(c: Capsule, ops: list[dict[str, Any]], expected_rev: int, agent: str, now: str) -> Capsule:
    """I-CAS: aplica operaciones set/add/remove/replace sobre una revisión conocida."""
    if expected_rev != c.rev:
        raise ConflictError(f"rev esperada {expected_rev} pero la actual es {c.rev}: releer y reintentar")
    require_slug(agent, "agent")
    changes: dict[str, Any] = {}
    lists: dict[str, list[Any]] = {k: list(getattr(c, k)) for k in _LIST_FIELDS}
    for op in ops:
        kind, fld = op.get("op"), op.get("field")
        if kind == "set" and fld in _SET_FIELDS:
            changes[fld] = int(op["value"]) if fld == "ttl_hours" else str(op["value"])
        elif kind in ("add", "remove", "replace") and fld in _LIST_FIELDS:
            conv = _as_pointer if fld == "punteros" else str
            if kind == "add":
                lists[fld].append(conv(op["value"]))
            elif kind == "replace":
                lists[fld] = [conv(v) for v in op["value"]]
            else:
                idx = int(op.get("index", -1))
                if not 0 <= idx < len(lists[fld]):
                    raise DomainError(f"índice fuera de rango en {fld}: {idx}")
                lists[fld].pop(idx)
        else:
            raise DomainError(f"operación inválida: {op!r}")
    clock = dict(c.clock)
    clock[agent] = clock.get(agent, 0) + 1
    kwargs: dict[str, Any] = dict(changes)
    kwargs.update({k: tuple(v) for k, v in lists.items()})
    return replace(c, **kwargs, rev=c.rev + 1, updated_at=now, clock=tuple(clock.items()))


def staleness(c: Capsule, now: str, current_hashes: Mapping[str, str | None]) -> dict[str, Any]:
    """Read-before-act: TTL vencido, punteros cuyo sha256 cambió y punteros cuyo archivo no existe."""
    age_h = (parse_iso(now) - parse_iso(c.updated_at)).total_seconds() / 3600.0
    stale, missing = [], []
    for p in c.punteros:
        cur = current_hashes.get(p.path)
        if cur is None:
            missing.append(p.path)
        elif p.sha256 and not cur.startswith(p.sha256):
            stale.append(p.path)
    expired = age_h > c.ttl_hours
    return {"expired": expired, "age_hours": round(age_h, 2), "stale_refs": stale, "missing": missing,
            "ok": not expired and not stale and not missing}


def compare_clocks(a: Mapping[str, int], b: Mapping[str, int]) -> str:
    """F22: relación causal entre dos relojes vectoriales."""
    keys = set(a) | set(b)
    le = all(a.get(k, 0) <= b.get(k, 0) for k in keys)
    ge = all(a.get(k, 0) >= b.get(k, 0) for k in keys)
    if le and ge:
        return "equal"
    if le:
        return "before"
    if ge:
        return "after"
    return "concurrent"
