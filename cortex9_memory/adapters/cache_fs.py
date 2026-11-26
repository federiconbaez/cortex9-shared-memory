"""Adaptador CacheStore: `<dir>/<key>.json` + `<dir>/<key>.txt`, escritura atómica con os.replace."""
from __future__ import annotations

import os
from pathlib import Path


class FsCacheStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str, ext: str) -> Path:
        if not key.isalnum():
            raise ValueError("clave inválida")
        return self.root / f"{key}.{ext}"

    def read(self, key: str) -> tuple[str | None, str | None]:
        m, c = self._p(key, "json"), self._p(key, "txt")
        if not (m.exists() and c.exists()):
            return None, None
        return m.read_text(encoding="utf-8"), c.read_text(encoding="utf-8")

    def write(self, key: str, meta: str, content: str) -> None:
        for ext, text in (("txt", content), ("json", meta)):
            dest = self._p(key, ext)
            tmp = dest.with_name(dest.name + f".tmp{os.getpid()}")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, dest)
