"""Capa 3 y 2: log append-only encadenado (F25), raíz Merkle y ledger CRDT de decisiones (F21). Puro."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .models import DomainError, require_slug
from .scoring import estimate_tokens, parse_iso
from .taint import TrustTier, assess

GENESIS = "0" * 64
KINDS = ("task_open", "decision", "contradiction", "handoff_sent", "handoff_ack", "artifact_built",
         "usage_snapshot", "taint_quarantine", "note")
MAX_PAYLOAD_TOKENS = 120
DECISION_STATUS = ("vigente", "supersedida", "revocada")
CONTRADICTION_STATUS = ("abierta", "resuelta")
_DEC_ID = re.compile(r"^D-\d{1,6}$")
_CON_ID = re.compile(r"^C-\d{1,6}$")


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


@dataclass(frozen=True)
class VerifyReport:
    valid: int
    last_sha256: str
    torn_offset: int | None
    broken_at: int | None
    hashes: list[str] = field(default_factory=list)
    merkle_root: str = ""

    @property
    def ok(self) -> bool:
        return self.torn_offset is None and self.broken_at is None


def merkle_root(hashes: list[str]) -> str:
    if not hashes:
        return _sha("")
    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [_sha(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def verify(text: str) -> VerifyReport:
    """I-LOG: valida la cadena. Una cola incompleta o ilegible al final es `torn`; un corte en el medio es `broken`."""
    raw = text.split("\n")
    complete, tail = raw[:-1], raw[-1]
    prev, offset = GENESIS, 0
    hashes: list[str] = []
    for idx, line in enumerate(complete, start=1):
        try:
            ev = json.loads(line)
            ok_json = isinstance(ev, dict)
        except json.JSONDecodeError:
            ok_json = False
        if not ok_json:
            is_last = idx == len(complete) and tail == ""
            if is_last:
                return VerifyReport(len(hashes), prev, offset, None, hashes, merkle_root(hashes))
            return VerifyReport(len(hashes), prev, None, idx, hashes, merkle_root(hashes))
        if ev.get("prev_sha256") != prev:
            return VerifyReport(len(hashes), prev, None, idx, hashes, merkle_root(hashes))
        prev = _sha(line)
        hashes.append(prev)
        offset += len(line.encode("utf-8")) + 1
    torn = offset if tail != "" else None
    return VerifyReport(len(hashes), prev, torn, None, hashes, merkle_root(hashes))


def build_next(current_text: str, *, ts: str, agent: str, kind: str, task_id: str | None = None,
               payload: dict[str, Any] | None = None, payload_ref: str | None = None) -> str:
    """Construye la próxima línea encadenada (con `\\n`). Se usa dentro del lock del adaptador."""
    try:
        parse_iso(ts)
    except ValueError as exc:
        raise DomainError(f"ts inválido {ts!r}") from exc
    require_slug(agent, "agent")
    if kind not in KINDS:
        raise DomainError(f"kind inválido {kind!r}; usar {KINDS}")
    body = payload or {}
    if estimate_tokens(_canon(body)) > MAX_PAYLOAD_TOKENS:
        raise DomainError(f"payload > {MAX_PAYLOAD_TOKENS} tokens: guardarlo en disco y pasar payload_ref")
    for s in _strings(body):
        v = assess(s, TrustTier.T1)
        if v.blocked:
            raise DomainError(f"payload bloqueado por taint-guard: {', '.join(v.reasons)}")
    if kind == "decision":
        _check_entry(body, _DEC_ID, DECISION_STATUS)
    elif kind == "contradiction":
        _check_entry(body, _CON_ID, CONTRADICTION_STATUS)
    rep = verify(current_text)
    if rep.torn_offset is not None:
        raise DomainError("cola rota en el log: correr event-verify --repair antes de agregar")
    if rep.broken_at is not None:
        raise DomainError(f"cadena rota en la línea {rep.broken_at}: revisar a mano, no se agrega")
    ev = {"ts": ts, "agent": agent, "kind": kind, "task_id": task_id, "payload": body, "payload_ref": payload_ref,
          "prev_sha256": rep.last_sha256}
    ev["event_id"] = _sha(rep.last_sha256 + _canon(ev))[:16]
    return _canon(ev) + "\n"


def _check_entry(body: dict[str, Any], id_re: re.Pattern[str], statuses: tuple[str, ...]) -> None:
    if not id_re.match(str(body.get("id", ""))):
        raise DomainError(f"id inválido {body.get('id')!r}")
    if not str(body.get("statement", "")).strip():
        raise DomainError("statement vacío")
    if body.get("status", statuses[0]) not in statuses:
        raise DomainError(f"status inválido {body.get('status')!r}; usar {statuses}")


# ---------------- Ledger CRDT (LWW por clave, desempate determinista) ----------------

def _stamp(e: dict[str, Any]) -> tuple[str, str, int, str]:
    """Orden total: tiempo, agente, posición en su log (desempata el mismo segundo) e id."""
    return (parse_iso(e["ts"]).isoformat(), e["agent"], int(e.get("seq", 0)), e["event_id"])


def fold_ledger(text: str) -> dict[str, dict[str, dict[str, Any]]]:
    state: dict[str, dict[str, dict[str, Any]]] = {"decisions": {}, "contradictions": {}}
    for seq, line in enumerate(text.splitlines(), start=1):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        bucket = {"decision": "decisions", "contradiction": "contradictions"}.get(ev.get("kind", ""))
        if not bucket:
            continue
        p = ev["payload"]
        entry = {"id": p["id"], "statement": p["statement"],
                 "status": p.get("status", DECISION_STATUS[0] if bucket == "decisions" else "abierta"),
                 "supersedes": p.get("supersedes"), "resolved_by": p.get("resolved_by"),
                 "ts": ev["ts"], "agent": ev["agent"], "event_id": ev["event_id"], "seq": seq}
        merged = merge_ledgers(state, {"decisions": {}, "contradictions": {}, bucket: {p["id"]: entry}})
        state = merged
    return state


def merge_ledgers(a: dict[str, dict[str, dict[str, Any]]],
                  b: dict[str, dict[str, dict[str, Any]]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Merge de estados: conmutativo, asociativo e idempotente (gana el sello mayor por clave)."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for bucket in ("decisions", "contradictions"):
        merged = dict(a.get(bucket, {}))
        for key, entry in b.get(bucket, {}).items():
            cur = merged.get(key)
            if cur is None or _stamp(entry) > _stamp(cur):
                merged[key] = entry
        out[bucket] = dict(sorted(merged.items(), key=lambda kv: int(kv[0].split("-")[1])))
    return out


def render_ledger(state: dict[str, dict[str, dict[str, Any]]]) -> str:
    out = ["# DECISIONS (vista generada del log; no editar a mano)", "", "## Decisiones",
           "| ID | Fecha UTC | Agente | Estado | Decisión |", "|---|---|---|---|---|"]
    for k, e in state["decisions"].items():
        extra = f" (supersede {e['supersedes']})" if e.get("supersedes") else ""
        out.append(f"| {k} | {e['ts']} | {e['agent']} | {e['status']} | {e['statement']}{extra} |")
    out += ["", "## Contradicciones", "| ID | Estado | Resuelta por | Descripción |", "|---|---|---|---|"]
    for k, e in state["contradictions"].items():
        out.append(f"| {k} | {e['status']} | {e.get('resolved_by') or '-'} | {e['statement']} |")
    return "\n".join(out) + "\n"
