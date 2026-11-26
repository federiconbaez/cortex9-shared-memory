"""F06: sobres de handoff por punteros con tope de tokens (I-HO). Puro."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .capsules import Pointer
from .models import DomainError, require_slug
from .scoring import estimate_tokens
from .taint import TrustTier, assess

MAX_FIELD_TOKENS = 120
MAX_HANDOFF_TOKENS = 280
MAX_RESULT_TOKENS = 180
MAX_FIELD_LINES = 15
RESULT_STATUS = ("done", "partial", "blocked", "aborted_budget")
_HO_ID = re.compile(r"^HO-[0-9A-Za-z-]{1,40}$")


def _text(value: Any, what: str, required: bool = True) -> str:
    s = str(value or "").strip()
    if required and not s:
        raise DomainError(f"{what} vacío")
    if s.count("\n") + 1 > MAX_FIELD_LINES or estimate_tokens(s) > MAX_FIELD_TOKENS:
        raise DomainError(f"{what}: contenido pegado; guardarlo en disco y pasar un puntero")
    v = assess(s, TrustTier.T1)
    if v.blocked:
        raise DomainError(f"{what} bloqueado por taint-guard: {', '.join(v.reasons)}")
    return s


def _pointers(values: Any, what: str) -> list[str]:
    out = []
    for ref in values or []:
        p = Pointer.parse(str(ref))
        if not p.sha256:
            raise DomainError(f"{what}: cada puntero necesita @sha256 ({ref!r})")
        out.append(p.ref())
    return out


def _total(env: dict[str, Any], cap: int) -> int:
    used = estimate_tokens(json.dumps(env, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if used > cap:
        raise DomainError(f"sobre de {used} tokens supera el tope de {cap}")
    return used


def validate_handoff(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not _HO_ID.match(str(raw.get("id", ""))):
        raise DomainError("id de handoff inválido (HO-...)")
    guard = dict(raw.get("budget_guard") or {})
    for k in ("max_delta_pct", "stop_if_weekly_pct_ge"):
        if k in guard and not 0 <= float(guard[k]) <= 100:
            raise DomainError(f"budget_guard.{k} fuera de [0, 100]")
    env = {
        "v": "handoff_v1",
        "id": raw["id"],
        "from": require_slug(str(raw.get("from", "")), "from"),
        "to": require_slug(str(raw.get("to", "")), "to"),
        "task_id": _text(raw.get("task_id"), "task_id"),
        "capsule": raw.get("capsule"),
        "goal": _text(raw.get("goal"), "goal"),
        "inputs": _pointers(raw.get("inputs"), "inputs"),
        "constraints": [_text(c, "constraint") for c in raw.get("constraints") or []],
        "acceptance": [_text(c, "acceptance") for c in raw.get("acceptance") or []],
        "budget_guard": guard,
    }
    return {"envelope": env, "tokens_est": _total(env, MAX_HANDOFF_TOKENS)}


def validate_result(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not _HO_ID.match(str(raw.get("id", ""))):
        raise DomainError("id de handoff inválido (HO-...)")
    if raw.get("status") not in RESULT_STATUS:
        raise DomainError(f"status inválido; usar {RESULT_STATUS}")
    env = {
        "v": "handoff_result_v1",
        "id": raw["id"],
        "from": require_slug(str(raw.get("from", "")), "from"),
        "status": raw["status"],
        "summary": _text(raw.get("summary"), "summary"),
        "artifacts": _pointers(raw.get("artifacts"), "artifacts"),
        "checks": [_text(c, "check") for c in raw.get("checks") or []],
        "next_recommended": _text(raw.get("next_recommended"), "next_recommended", required=False),
    }
    return {"envelope": env, "tokens_est": _total(env, MAX_RESULT_TOKENS)}


def stale_inputs(envelope: Mapping[str, Any], current_hashes: Mapping[str, str | None]) -> list[str]:
    """Read-before-act del receptor: punteros cuyo archivo cambió o ya no existe."""
    stale = []
    for ref in envelope.get("inputs", []):
        p = Pointer.parse(ref)
        cur = current_hashes.get(p.path)
        if cur is None or (p.sha256 and not cur.startswith(p.sha256)):
            stale.append(p.path)
    return stale
