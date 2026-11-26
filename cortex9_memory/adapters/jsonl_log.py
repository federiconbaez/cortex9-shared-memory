"""Adaptador AppendLog: archivo JSONL append-only con flock exclusivo y fsync."""
from __future__ import annotations

import fcntl
import os
from collections.abc import Callable
from pathlib import Path


class JsonlFileLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read_text(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    def append_atomic(self, build: Callable[[str], str]) -> str:
        with open(self.path, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                current = fh.read()
                chunk = build(current)
                if chunk:
                    fh.seek(0, os.SEEK_END)
                    fh.write(chunk)
                    fh.flush()
                    os.fsync(fh.fileno())
                return chunk
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def truncate_to(self, size: int) -> None:
        """Recorta una cola rota (lo decide el dominio; el adaptador solo ejecuta)."""
        with open(self.path, "r+b") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.truncate(size)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
