"""Adaptador de cápsulas en disco: escritura atómica (tmp + fsync + rename) con CAS bajo flock."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

from ..domain.capsules import ConflictError


class FsCapsuleStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        return self.root / f"{task_id}.md"

    def read(self, task_id: str) -> str | None:
        p = self._path(task_id)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def write(self, task_id: str, text: str, expected: str | None) -> None:
        lock = self.root / ".capsules.lock"
        with open(lock, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                current = self.read(task_id)
                if current != expected:
                    raise ConflictError(f"la cápsula {task_id} cambió en disco: releer y reintentar")
                tmp = self.root / f".{task_id}.md.tmp-{os.getpid()}"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(text)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self._path(task_id))
                dfd = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.md") if not p.name.startswith("."))
