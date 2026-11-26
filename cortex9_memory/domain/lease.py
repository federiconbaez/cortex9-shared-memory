"""F44: mutex de trabajo pesado entre bots como lease con TTL. Puro."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

from .models import DomainError, require_slug
from .scoring import parse_iso


@dataclass(frozen=True)
class Lease:
    agent: str
    task_id: str
    kind: str
    acquired_at: str
    expires_at: str

    def __post_init__(self) -> None:
        require_slug(self.agent, "agent")
        if not self.task_id.strip():
            raise DomainError("task_id vacío")
        if parse_iso(self.expires_at) <= parse_iso(self.acquired_at):
            raise DomainError("expires_at > acquired_at")

    def active(self, now: str) -> bool:
        return parse_iso(self.expires_at) > parse_iso(now)

    def owned_by(self, agent: str, task_id: str) -> bool:
        return self.agent == agent and self.task_id == task_id

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: str | None) -> Lease | None:
        if not text or not text.strip():
            return None
        try:
            d: dict[str, Any] = json.loads(text)
            return cls(**d)
        except (json.JSONDecodeError, TypeError, DomainError):
            return None  # lease ilegible = libre (se reemplaza al adquirir)


def _iso(dt: Any) -> str:
    return str(dt.isoformat()).replace("+00:00", "Z")


def blocker(current: Lease | None, agent: str, task_id: str, now: str) -> Lease | None:
    if current and current.active(now) and not current.owned_by(agent, task_id):
        return current
    return None


def acquire(current: Lease | None, agent: str, task_id: str, kind: str, now: str,
            ttl_min: int) -> tuple[Lease | None, Lease | None]:
    """I-LEASE: devuelve (lease_concedido, bloqueador)."""
    b = blocker(current, agent, task_id, now)
    if b:
        return None, b
    t = parse_iso(now)
    acquired = current.acquired_at if current and current.owned_by(agent, task_id) and current.active(now) else now
    return Lease(agent, task_id, kind, acquired, _iso(t + timedelta(minutes=ttl_min))), None


def release(current: Lease | None, agent: str, task_id: str, now: str, force: bool = False) -> tuple[bool, str]:
    if current is None or not current.active(now):
        return True, "libre"
    if current.owned_by(agent, task_id) or force:
        return True, "liberado"
    return False, f"en manos de {current.agent}/{current.task_id}"
