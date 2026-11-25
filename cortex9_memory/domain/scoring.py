"""Cálculo puro de relevancia híbrida (FTS5 + confianza + decay temporal + uso) y empaquetado por presupuesto."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


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
    used = estimate_tokens(lines[0])
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
        head_t = estimate_tokens(sec_head)
        added: list[str] = []
        sec_used = 0
        for idx, line in enumerate(rows):
            lt = estimate_tokens(line)
            need = (head_t if not added else 0) + sec_used + lt
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
