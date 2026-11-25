"""Extracción pura de instrumentos `(XXXX-FNBR)` desde prosa Markdown."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import NOT_FOUND

_HEADS = r"(?:Teorema|Teoría|Índice|Indice|Regla|Marco|Framework|Ley|Principio|Función|Funcion|Coeficiente|Test|Protocolo|Factor)"
_PATTERN = re.compile(
    r"(?P<name>" + _HEADS + r"\b[^()\n.;:]{2,140}?)\s*\((?P<acr>[TRIF][A-Z0-9]{1,11}-FNBR)\)"
)
_SENT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class HarvestedInstrument:
    acronym: str
    name: str
    kind: str
    definition: str
    refutation: str


def _paragraph_at(text: str, pos: int) -> str:
    start = text.rfind("\n\n", 0, pos)
    end = text.find("\n\n", pos)
    return text[start + 2 if start >= 0 else 0: end if end >= 0 else len(text)]


def harvest(text: str, max_def_chars: int = 900) -> list[HarvestedInstrument]:
    """Devuelve un instrumento por acrónimo (primera aparición).

    definición = el texto desde el acrónimo hasta completar ~max_def_chars por
    oraciones dentro del mismo párrafo; refutación = primera oración del párrafo
    que contenga 'refut' o 'falsa', si no `NO ENCONTRADO`.
    """
    seen: set[str] = set()
    out: list[HarvestedInstrument] = []
    for m in _PATTERN.finditer(text):
        acr = m.group("acr")
        if acr in seen:
            continue
        seen.add(acr)
        raw = m.group("name")
        heads = list(re.finditer(r"\b" + _HEADS + r"\b", raw))
        if heads:  # nombre = desde la última cabeza (evita arrastrar otra mención previa)
            raw = raw[heads[-1].start():]
        name = " ".join(raw.split())
        para = _paragraph_at(text, m.start())
        after = para[para.find(f"({acr})") + len(acr) + 2:].lstrip(" ,.")
        sentences = [s for s in _SENT.split(after) if s.strip()]
        definition = ""
        for s in sentences:
            if definition and len(definition) + len(s) > max_def_chars:
                break
            definition = (definition + " " + s).strip()
        definition = definition or name
        refutation = next((s.strip() for s in _SENT.split(para)
                           if re.search(r"refut|falsa", s, re.I)), NOT_FOUND)
        out.append(HarvestedInstrument(acr, name, acr[0], definition, refutation))
    return out
