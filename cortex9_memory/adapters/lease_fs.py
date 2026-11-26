"""Adaptador LeaseStore: archivo JSON con flock exclusivo sobre `<path>.lock` y reemplazo atómico."""
from __future__ import annotations

import fcntl
import os
from collections.abc import Callable
from pathlib import Path


class FsLeaseStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = self.path.with_name(self.path.name + ".lock")

    def read(self) -> str | None:
        return self.path.read_text(encoding="utf-8") if self.path.exists() else None

    def update(self, fn: Callable[[str | None], str | None]) -> str | None:
        with open(self.lock, "a+") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                cur = self.read()
                new = fn(cur)
                if new is None:
                    if self.path.exists():
                        self.path.unlink()
                elif new != cur:
                    tmp = self.path.with_name(self.path.name + f".tmp{os.getpid()}")
                    with open(tmp, "w", encoding="utf-8") as fh:
                        fh.write(new)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, self.path)
                return cur
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
