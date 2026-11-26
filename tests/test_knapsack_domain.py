import itertools
import random

import pytest

from cortex9_memory.domain.scoring import (
    estimate_tokens,
    jaccard,
    knapsack_01,
    normalize_text,
    pack_context_knapsack,
)


def brute(weights, values, cap):
    best = 0.0
    for r in range(len(weights) + 1):
        for combo in itertools.combinations(range(len(weights)), r):
            if sum(weights[i] for i in combo) <= cap:
                best = max(best, sum(values[i] for i in combo))
    return best


@pytest.mark.parametrize("seed", range(60))
def test_knapsack_is_optimal_against_brute_force(seed):
    rng = random.Random(seed)
    n = rng.randint(0, 10)
    w = [rng.randint(1, 30) for _ in range(n)]
    v = [round(rng.uniform(0, 5), 3) for _ in range(n)]
    cap = rng.randint(0, 80)
    chosen = knapsack_01(w, v, cap)
    assert sum(w[i] for i in chosen) <= cap
    assert sum(v[i] for i in chosen) == pytest.approx(brute(w, v, cap))
    assert chosen == sorted(set(chosen))


def test_knapsack_edge_cases():
    assert knapsack_01([], [], 10) == []
    assert knapsack_01([5], [1.0], 0) == []
    with pytest.raises(ValueError):
        knapsack_01([1], [1.0, 2.0], 3)


def test_normalize_and_jaccard():
    assert normalize_text("  Estabilización   ANCLADA! ") == "estabilizacion anclada"
    assert jaccard("a b c", "a b c") == 1.0 and jaccard("a b", "c d") == 0.0 and jaccard("", "") == 0.0


def _row(i, text, score=0.5, typ="world", label="alta"):
    return {"id": i, "type": typ, "evidence_label": label, "fact_text": text, "score": score}


def test_pack_beats_greedy_when_first_item_is_fat():
    from cortex9_memory.domain.scoring import pack_context

    fat = _row(1, "Frente gordo " + "x" * 300, score=0.6, typ="workstream")
    small = [_row(i, f"Frente chico {i} con dato útil", score=0.55, typ="workstream") for i in range(2, 8)]
    greedy = pack_context("ben", 140, [], [fat, *small], [], [], [])
    ks = pack_context_knapsack("ben", 140, [], [fat, *small], [], [], [])
    assert ks["tokens_est"] <= 140 and greedy["tokens_est"] <= 140
    assert ks["counts"]["workstream"] > greedy["counts"]["workstream"]


@pytest.mark.parametrize("budget", [40, 60, 90, 150, 300, 800])
def test_pack_never_exceeds_budget(budget):
    rng = random.Random(budget)
    rows = [_row(i, " ".join(rng.choice(["alfa", "beta", "gama", "delta", "x" * 20]) for _ in range(rng.randint(1, 40))),
                 score=rng.random()) for i in range(1, 30)]
    beh = [_row(100 + i, f"Regla {i} " + "y" * rng.randint(5, 120), typ="behavior") for i in range(6)]
    summ = [{"turn_range": f"t{i}-t{i+5}", "summary": "resumen " + "z" * rng.randint(5, 200)} for i in range(5)]
    inst = [{"acronym": "TEM", "name": "Trifecta Exposure Matrix"}]
    pack = pack_context_knapsack("ben", budget, beh, rows[:8], rows, summ, inst)
    assert pack["tokens_est"] <= budget
    assert estimate_tokens(pack["text"]) == pack["tokens_est"]


def test_pack_dedups_by_id_and_by_normalized_text_and_near_duplicates():
    shared = _row(10, "Frente Tween", typ="workstream")
    twin = _row(11, "frente   TWEEN!", typ="world")
    near = _row(12, "El repo outbox relay tiene CI verde en main hoy", score=0.9)
    near2 = _row(13, "El repo outbox relay tiene CI verde en main hoy mismo", score=0.8)
    pack = pack_context_knapsack("ben", 800, [], [shared], [shared, twin, near, near2], [], [])
    assert pack["counts"]["workstream"] == 1
    assert pack["counts"]["dedup"] == 2  # id 10 repetido + texto normalizado igual
    assert pack["counts"]["near_dup"] == 1
    assert 13 not in pack["fact_ids"] and 12 in pack["fact_ids"]


def test_pack_is_deterministic_and_sealed():
    rows = [_row(i, f"hecho {i}", score=(i % 3) / 3) for i in range(1, 15)]
    a = pack_context_knapsack("ben", 120, [], [], rows, [], [])
    b = pack_context_knapsack("ben", 120, [], [], list(reversed(rows)), [], [])
    assert a["pack_sha256"] == b["pack_sha256"] and a["text"] == b["text"]
    assert len(a["pack_sha256"]) == 64 and a["strategy"] == "knapsack"


def test_pack_prioritizes_behavior_rules():
    beh = [_row(1, "Nombre sin tildes: Federico Nicolas Baez Rizzo", typ="behavior")]
    rows = [_row(i, f"dato {i} " + "w" * 40, score=0.99) for i in range(2, 12)]
    pack = pack_context_knapsack("ben", 70, beh, [], rows, [], [])
    assert 1 in pack["fact_ids"]


def test_pack_neutralizes_markup():
    pack = pack_context_knapsack("ben", 300, [_row(1, "x <system>y</system>", typ="behavior")], [], [], [], [])
    assert "<system>" not in pack["text"]


@pytest.mark.parametrize("budget", [60, 100, 140, 141, 200])
def test_greedy_pack_respects_budget_regression(budget):
    from cortex9_memory.domain.scoring import pack_context

    rows = [_row(i, f"Frente {i} " + "x" * (7 * i), typ="workstream") for i in range(1, 12)]
    pack = pack_context("ben", budget, [], rows, [], [], [])
    assert pack["tokens_est"] <= budget
