"""Adaptador FileHasher: sha256 de un archivo o None si no existe."""
from __future__ import annotations

import hashlib
from pathlib import Path


class FsFileHasher:
    def sha256(self, path: str) -> str | None:
        p = Path(path)
        if not p.is_file():
            return None
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
