"""Cálculo puro de relevancia híbrida (FTS5 + confianza + decay temporal + uso) y empaquetado por presupuesto."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .taint import neutralize


def parse_iso(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def recency_decay(created_at: str, now: str, half_life_days: float = 30.0) -> float:
    if half_life_days <= 0:
        return 1.0
    age_days = max(0.0, (parse_iso(now) - parse_iso(created_at)).total_seconds() / 86400.0)
    return math.pow(0.5, age_days / half_life_days)


def hybrid_score(
    bm25_raw: float | None,
    confidence: float,
    reference_ts: str,
    now: str,
    access_count: int = 0,
    half_life_days: float = 30.0,
) -> float:
    """Combina ajuste léxico, confianza epistémica, decay por recencia y refuerzo acotado por uso."""
    decay = recency_decay(reference_ts, now, half_life_days)
    access_boost = min(0.2, 0.04 * math.log1p(max(0, access_count)))
    if bm25_raw is None:
        return round(0.65 * confidence + 0.35 * decay + access_boost, 6)
    # SQLite bm25() es <= 0 cuanto mejor es el match.
    lexical = 1.0 / (1.0 + math.exp(-max(0.0, -float(bm25_raw))))
    # Normalizamos de [0.5, 1.0) a [0.0, 1.0)
    lexical = max(0.0, (lexical - 0.5) * 2.0)
    return round(0.45 * lexical + 0.30 * confidence + 0.25 * decay + access_boost, 6)


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def pack_context(
    agent: str,
    budget_tokens: int,
    behaviors: list[dict[str, Any]],
    workstreams: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    instruments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Construye un bloque compacto respetando estrictamente `budget_tokens`."""
    header = f"# CONTEXT PACK (agent={agent}, budget={budget_tokens}t)\n"
    lines: list[str] = [header.rstrip("\n")]
    # Cada pieza cuenta su salto de línea: la suma de techos acota el total (I-PACK).
    used = estimate_tokens(lines[0] + "\n")
    counts = {
        "behavior": 0,
        "workstream": 0,
        "match": 0,
        "matched_total": len(matches),
        "dedup": 0,
        "summary": 0,
        "instrument": 0,
    }
    seen_fact_ids: set[int] = set()

    def try_add_section(title: str, rows: list[str], key: str, ids: list[int] | None = None) -> None:
        nonlocal used
        if not rows:
            return
        sec_head = f"\n## {title}"
        head_t = estimate_tokens(sec_head + "\n")
        added: list[str] = []
        sec_used = 0
        for idx, raw in enumerate(rows):
            line = neutralize(raw)
            lt = estimate_tokens(line + "\n")
            need = head_t + sec_used + lt
            if used + need > budget_tokens:
                continue
            added.append(line)
            sec_used += lt
            if ids is not None:
                seen_fact_ids.add(ids[idx])
        if added:
            lines.append(sec_head)
            lines.extend(added)
            used += head_t + sec_used
            counts[key] += len(added)

    try_add_section(
        "Reglas (behavior)",
        [f"- [{r['id']}] ({r['evidence_label']}) {r['fact_text']}" for r in behaviors],
        "behavior",
        [int(r["id"]) for r in behaviors],
    )
    ws_dedup = sum(1 for r in workstreams if int(r["id"]) in seen_fact_ids)
    ws_filtered = [r for r in workstreams if int(r["id"]) not in seen_fact_ids]
    try_add_section(
        "Frentes activos (workstream)",
        [f"- [{r['id']}] ({r['evidence_label']}) {r['fact_text']}" for r in ws_filtered],
        "workstream",
        [int(r["id"]) for r in ws_filtered],
    )
    m_dedup = sum(1 for r in matches if int(r["id"]) in seen_fact_ids)
    counts["dedup"] = ws_dedup + m_dedup
    m_filtered = [r for r in matches if int(r["id"]) not in seen_fact_ids]
    try_add_section(
        "Hechos relevantes",
        [f"- [{r['id']}] {r['type']} ({r['evidence_label']}) {r['fact_text']}" for r in m_filtered],
        "match",
        [int(r["id"]) for r in m_filtered],
    )
    try_add_section(
        "Resúmenes recientes",
        [f"- [{r['turn_range']}] {r['summary']}" for r in summaries],
        "summary",
    )
    try_add_section(
        "Instrumentos relacionados",
        [f"- {r['acronym']}: {r['name']}" for r in instruments],
        "instrument",
    )

    text = "\n".join(lines) + "\n"
    return {
        "agent": agent,
        "budget_tokens": budget_tokens,
        "tokens_est": estimate_tokens(text),
        "counts": counts,
        "fact_ids": sorted(seen_fact_ids),
        "text": text,
    }


# ---------------------------------------------------------------------------
# F15 mochila 0-1, F17 MMR, F05 dedup por texto normalizado, F38 sello de replay
# ---------------------------------------------------------------------------

_SECTION_TITLES = (
    ("behavior", "Reglas (behavior)"),
    ("workstream", "Frentes activos (workstream)"),
    ("match", "Hechos relevantes"),
    ("summary", "Resúmenes recientes"),
    ("instrument", "Instrumentos relacionados"),
)
NEAR_DUP_THRESHOLD = 0.9
MMR_LAMBDA = 0.7


def normalize_text(text: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text.lower())
    no_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    words = "".join(ch if ch.isalnum() else " " for ch in no_marks).split()
    return " ".join(words)


def jaccard(a: str, b: str) -> float:
    sa, sb = set(normalize_text(a).split()), set(normalize_text(b).split())
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def knapsack_01(weights: list[int], values: list[float], capacity: int) -> list[int]:
    """Mochila 0-1 exacta por programación dinámica. Devuelve índices elegidos en orden creciente."""
    if len(weights) != len(values):
        raise ValueError("weights y values deben tener el mismo largo")
    n, cap = len(weights), max(0, capacity)
    best = [0.0] * (cap + 1)
    take = [bytearray(cap + 1) for _ in range(n)]
    for i in range(n):
        w, v = weights[i], values[i]
        if w > cap or v <= 0:
            continue
        for c in range(cap, w - 1, -1):
            cand = best[c - w] + v
            if cand > best[c] + 1e-12:
                best[c] = cand
                take[i][c] = 1
    chosen, c = [], cap
    for i in range(n - 1, -1, -1):
        if take[i][c]:
            chosen.append(i)
            c -= weights[i]
    return sorted(chosen)


def pack_context_knapsack(
    agent: str,
    budget_tokens: int,
    behaviors: list[dict[str, Any]],
    workstreams: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    instruments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Context pack óptimo bajo presupuesto (I-PACK): dedup, MMR y mochila 0-1 con costo de encabezados."""
    import hashlib

    header = f"# CONTEXT PACK (agent={agent}, budget={budget_tokens}t, strategy=knapsack)\n"
    counts = {k: 0 for k, _ in _SECTION_TITLES}
    counts.update({"matched_total": len(matches), "dedup": 0, "near_dup": 0})
    cands: list[dict[str, Any]] = []

    def fact_line(r: dict[str, Any], with_type: bool) -> str:
        t = f"{r['type']} " if with_type else ""
        return f"- [{r['id']}] {t}({r.get('evidence_label')}) {r['fact_text']}"

    order = {k: i for i, (k, _) in enumerate(_SECTION_TITLES)}
    for r in behaviors:
        cands.append({"sec": "behavior", "id": int(r["id"]), "text": r["fact_text"],
                      "line": fact_line(r, False), "value": 10.0 + float(r.get("confidence") or 0.0)})
    for r in workstreams:
        cands.append({"sec": "workstream", "id": int(r["id"]), "text": r["fact_text"],
                      "line": fact_line(r, False), "value": 1.0 + 2.0 * float(r.get("score") or 0.5)})
    for r in matches:
        cands.append({"sec": "match", "id": int(r["id"]), "text": r["fact_text"],
                      "line": fact_line(r, True), "value": 1.0 + 3.0 * float(r.get("score") or 0.5)})
    for rank, r in enumerate(summaries):
        cands.append({"sec": "summary", "id": None, "text": r["summary"],
                      "line": f"- [{r['turn_range']}] {r['summary']}", "value": 1.2 * (0.85 ** rank)})
    for r in instruments:
        cands.append({"sec": "instrument", "id": None, "text": f"{r['acronym']} {r['name']}",
                      "line": f"- {r['acronym']}: {r['name']}", "value": 0.3})
    # Orden canónico: el resultado no depende del orden de entrada.
    cands.sort(key=lambda c: (order[c["sec"]], -c["value"], c["id"] if c["id"] is not None else -1, c["line"]))

    kept: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    seen_norm: set[str] = set()
    for c in cands:
        norm = normalize_text(c["text"])
        if (c["id"] is not None and c["id"] in seen_ids) or norm in seen_norm:
            counts["dedup"] += 1
            continue
        sim = max((jaccard(c["text"], k["text"]) for k in kept), default=0.0)
        if sim >= NEAR_DUP_THRESHOLD:
            counts["near_dup"] += 1
            continue
        c["value"] *= 1.0 - MMR_LAMBDA * sim
        c["line"] = neutralize(c["line"])
        c["w"] = estimate_tokens(c["line"] + "\n")
        kept.append(c)
        if c["id"] is not None:
            seen_ids.add(c["id"])
        seen_norm.add(norm)

    head_w = {k: estimate_tokens(f"\n## {title}\n") for k, title in _SECTION_TITLES}
    base = estimate_tokens(header)
    present = {c["sec"] for c in kept}
    cap = budget_tokens - base - sum(head_w[s] for s in present)
    chosen_idx = set(knapsack_01([c["w"] for c in kept], [c["value"] for c in kept], cap)) if cap > 0 else set()
    used = base + sum(kept[i]["w"] for i in chosen_idx)
    used += sum(head_w[s] for s in {kept[i]["sec"] for i in chosen_idx})
    # Relleno: lo liberado por secciones vacías se ocupa por densidad de valor.
    rest = sorted((i for i in range(len(kept)) if i not in chosen_idx),
                  key=lambda i: (-kept[i]["value"] / kept[i]["w"], i))
    for i in rest:
        secs = {kept[j]["sec"] for j in chosen_idx}
        extra = kept[i]["w"] + (0 if kept[i]["sec"] in secs else head_w[kept[i]["sec"]])
        if used + extra <= budget_tokens:
            chosen_idx.add(i)
            used += extra

    parts = [header]
    fact_ids: list[int] = []
    value_total = 0.0
    for sec, title in _SECTION_TITLES:
        rows = [kept[i] for i in sorted(chosen_idx) if kept[i]["sec"] == sec]
        if not rows:
            continue
        parts.append(f"\n## {title}\n")
        for c in rows:
            parts.append(c["line"] + "\n")
            value_total += c["value"]
            if c["id"] is not None:
                fact_ids.append(c["id"])
        counts[sec] = len(rows)
    text = "".join(parts)
    return {
        "agent": agent,
        "budget_tokens": budget_tokens,
        "tokens_est": estimate_tokens(text),
        "counts": counts,
        "fact_ids": sorted(fact_ids),
        "text": text,
        "strategy": "knapsack",
        "value_total": round(value_total, 6),
        "pack_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
