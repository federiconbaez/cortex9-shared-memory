"""F02 codebook sin pérdida, F07 plegado de salidas de herramientas, F35 sonda de fidelidad. Puro."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .models import DomainError
from .scoring import estimate_tokens, normalize_text

_ALIAS_NAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_TOKEN = re.compile(r"@@|@\{([A-Za-z0-9_]{1,16})\}")


@dataclass(frozen=True)
class Alias:
    name: str
    expansion: str

    def __post_init__(self) -> None:
        if not _ALIAS_NAME.match(self.name):
            raise DomainError(f"alias inválido {self.name!r} ([A-Za-z0-9_], <= 16)")
        if len(self.expansion) < 8 or any(ch in self.expansion for ch in "@{}\n"):
            raise DomainError("la expansión debe tener >= 8 caracteres y no contener @, {, } ni saltos")


def _book(aliases: Iterable[Alias]) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in aliases:
        if a.name in out and out[a.name] != a.expansion:
            raise DomainError(f"alias duplicado con otra expansión: {a.name}")
        out[a.name] = a.expansion
    return out


def encode(text: str, aliases: Iterable[Alias]) -> str:
    """I-CB: escapa '@' y reemplaza expansiones (la más larga primero) por @{alias}."""
    book = _book(aliases)
    out = text.replace("@", "@@")
    for name, exp in sorted(book.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        out = out.replace(exp, "@{" + name + "}")
    return out


def decode(text: str, aliases: Iterable[Alias]) -> str:
    book = _book(aliases)

    def sub(m: re.Match[str]) -> str:
        if m.group(0) == "@@":
            return "@"
        name = m.group(1)
        if name not in book:
            raise DomainError(f"alias desconocido @{{{name}}}")
        return book[name]

    return _TOKEN.sub(sub, text)


# ---------------- F07: plegado de salidas ----------------
_SUMMARY = re.compile(
    r"(\b\d+\s+(passed|failed|errors?|skipped|warnings?)\b)|^=+.*=+$|^(ok|FAIL|PASS)\b|^Tests?:|\bexit(?: code)? \d+",
    re.IGNORECASE,
)
_FAILURE = re.compile(r"^(FAILED|ERROR)\b|^E\s{2,}|\bError\b:|Error:|Exception\b|^panic:|--- FAIL|^Traceback")


def fold_output(text: str, ref: str | None = None, max_lines: int = 40, head: int = 3, tail: int = 5,
                max_failures: int = 8) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines()
    before = estimate_tokens(text) if text else 0
    if len(lines) <= max_lines:
        return text, {"tokens_before": before, "tokens_after": before, "omitted": 0, "failures": 0}
    keep: list[str] = []
    seen: set[str] = set()

    def add(line: str) -> None:
        key = normalize_text(line)
        if key not in seen:
            seen.add(key)
            keep.append(line)

    for ln in lines[:head]:
        add(ln)
    failures = [ln for ln in lines if _FAILURE.search(ln)]
    uniq_fail: list[str] = []
    for ln in failures:
        if normalize_text(ln) not in {normalize_text(u) for u in uniq_fail}:
            uniq_fail.append(ln)
    for ln in uniq_fail[:max_failures]:
        add(ln)
    for ln in lines:
        if _SUMMARY.search(ln):
            add(ln)
    for ln in lines[-tail:]:
        add(ln)
    omitted = len(lines) - len(keep)
    note = f"[… {omitted} líneas omitidas"
    if len(uniq_fail) > max_failures:
        note += f"; {len(uniq_fail) - max_failures} fallas únicas más"
    note += f"; completo en {ref}]" if ref else "]"
    folded = "\n".join(keep + [note])
    return folded, {"tokens_before": before, "tokens_after": estimate_tokens(folded), "omitted": omitted,
                    "failures": len(uniq_fail)}


# ---------------- F35: sonda de fidelidad ----------------
_URL = re.compile(r"https?://[^\s)>\]]+")
_PATTERNS = (
    re.compile(r"(?<![\w./-])/(?:[\w.\-]+/)*[\w.\-]+"),
    re.compile(r"\b[DC]-\d+\b"),
    re.compile(r"\bCX9-\d+\b"),
    re.compile(r"\b\d+(?:[.,]\d+)?%"),
    re.compile(r"\bt\d+[us]\d*\b"),
)
_HEX = re.compile(r"\b[0-9a-f]{7,64}\b")


def critical_entities(text: str) -> set[str]:
    out: set[str] = set()
    rest = text
    for m in _URL.finditer(text):
        out.add(m.group(0).rstrip(".,;:"))
    rest = _URL.sub(" ", text)
    for pat in _PATTERNS:
        out |= {m.group(0).rstrip(".,;:") for m in pat.finditer(rest)}
    for m in _HEX.finditer(rest):
        h = m.group(0)
        if re.search(r"[0-9]", h) and re.search(r"[a-f]", h):
            out.add(h)
    return out


def probe(source: str, summary: str, aliases: Iterable[Alias] = ()) -> dict[str, Any]:
    """I-PROBE: el resumen tiene que conservar cada entidad crítica del original."""
    plain = decode(summary, list(aliases))
    have = critical_entities(plain)
    missing = []
    for ent in sorted(critical_entities(source)):
        if ent in plain:
            continue
        if _HEX.fullmatch(ent) and any(
            _HEX.fullmatch(h) and min(len(h), len(ent)) >= 7 and (h.startswith(ent) or ent.startswith(h))
            for h in have
        ):
            continue
        missing.append(ent)
    return {"ok": not missing, "missing": missing, "checked": len(critical_entities(source))}
