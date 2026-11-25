import pytest

from cortex9_memory.domain.harvest import harvest
from cortex9_memory.domain.models import (
    Agent,
    Archetype,
    DomainError,
    Instrument,
    MemoryFact,
    MessageSummary,
    Relation,
    Skill,
    label_for_score,
)


def test_fact_valid_and_canonical_meta():
    f = MemoryFact("workstream", "  texto  ", 0.9, meta_json={"b": 1, "a": 2})
    assert f.meta_json == '{"a": 2, "b": 1}'
    assert f.resolved_label() == "alta"


@pytest.mark.parametrize("kw", [
    dict(type="otro", fact_text="x"),
    dict(type="world", fact_text="   "),
    dict(type="world", fact_text="x", confidence=1.5),
    dict(type="world", fact_text="x", meta_json="[no json"),
    dict(type="world", fact_text="x", meta_json='"str"'),
    dict(type="world", fact_text="x", evidence_label="dudosa"),
    dict(type="world", fact_text="x", valid_from="2026-02-01", valid_until="2026-01-01"),
])
def test_fact_invariants(kw):
    with pytest.raises(DomainError):
        MemoryFact(**kw)


@pytest.mark.parametrize("score,label", [(1, "alta"), (0.85, "alta"), (0.7, "media"), (0.3, "estimacion"), (0, "no_encontrado")])
def test_label_scale(score, label):
    assert label_for_score(score) == label


def test_slugs():
    Agent("agent-two")
    for bad in ("Ben", "ben_x", "", "-x"):
        with pytest.raises(DomainError):
            Agent(bad)
    with pytest.raises(DomainError):
        Archetype("Mal Slug", "n", "d")
    with pytest.raises(DomainError):
        Skill("ok", "t", "  ")


def test_instrument_rules():
    Instrument("TEME-FNBR", "Teorema X", "T", "def", "refuta si...")
    with pytest.raises(DomainError):
        Instrument("TEME-FNBR", "Teorema X", "I", "def")  # kind ≠ letra
    with pytest.raises(DomainError):
        Instrument("XYZ", "n", "T", "def")
    with pytest.raises(DomainError):
        Instrument("ICA-FNBR", "n", "I", "def", refutation=" ")
    assert Instrument("ICA-FNBR", "n", "I", "d").refutation == "NO ENCONTRADO"


def test_relation_rules():
    with pytest.raises(DomainError):
        Relation("planeta", 1, "x", "fact", 2)
    with pytest.raises(DomainError):
        Relation("fact", 1, "x", "fact", 1)
    with pytest.raises(DomainError):
        Relation("fact", 1, " ", "fact", 2)


def test_summary_tokens():
    assert MessageSummary.estimate_tokens("a" * 400) == 100
    with pytest.raises(DomainError):
        MessageSummary(1, "t1-t2", "s", tokens_est=-1)


TEXT = """Intro sin instrumentos.

Del análisis previo derivo el Teorema del Eslabón Mínimo y construyo el segundo instrumento, el Índice de Fragilidad del Valle (IFV-FNBR). Lo defino como la razón entre A y B. Se refuta si A no anticipa B.

Otro párrafo con la Regla de Cobertura (RCC-FNBR), que exige X. Más texto.

Repetición (IFV-FNBR) ignorada."""


def test_harvest():
    out = {h.acronym: h for h in harvest(TEXT)}
    assert set(out) == {"IFV-FNBR", "RCC-FNBR"}
    ifv = out["IFV-FNBR"]
    assert ifv.name == "Índice de Fragilidad del Valle"
    assert ifv.kind == "I"
    assert ifv.definition.startswith("Lo defino")
    assert ifv.refutation.startswith("Se refuta")
    assert out["RCC-FNBR"].refutation == "NO ENCONTRADO"


def test_confidence_derived_from_label():
    assert MemoryFact("world", "x", evidence_label="alta").confidence == 0.9
    assert MemoryFact("world", "x", evidence_label="no_encontrado").confidence == 0.0
    assert MemoryFact("world", "x").confidence == 0.8


def test_hybrid_score_decay_and_access():
    from cortex9_memory.domain.scoring import hybrid_score

    now = "2026-11-25T10:00:00Z"
    recent = hybrid_score(-2.5, 0.9, "2026-11-24T10:00:00Z", now, access_count=0)
    old = hybrid_score(-2.5, 0.9, "2026-08-01T10:00:00Z", now, access_count=0)
    boosted = hybrid_score(-2.5, 0.9, "2026-08-01T10:00:00Z", now, access_count=10)
    assert recent > old
    assert boosted > old


def test_pack_context_respects_budget():
    from cortex9_memory.domain.scoring import pack_context

    behaviors = [
        {"id": i, "evidence_label": "alta", "fact_text": f"Regla importante número {i} " + ("x" * 60)}
        for i in range(1, 10)
    ]
    pack = pack_context("companion", 120, behaviors, [], [], [], [])
    assert pack["tokens_est"] <= 120
    assert pack["counts"]["behavior"] >= 1
    assert pack["counts"]["behavior"] < 9


def test_source_key_and_temporal_relation_validation():
    with pytest.raises(DomainError):
        MemoryFact("world", "x", source_key="   ")
    with pytest.raises(DomainError):
        Relation("fact", 1, "r", "fact", 2, valid_from="2026-11-20T00:00:00Z", valid_until="2026-11-10T00:00:00Z")



def test_pack_context_reports_dedup_and_matched_total():
    from cortex9_memory.domain.scoring import pack_context

    shared = {"id": 10, "type": "workstream", "evidence_label": "alta", "fact_text": "Frente Tween"}
    other = {"id": 11, "type": "world", "evidence_label": "media", "fact_text": "Otro hecho Tween"}
    pack = pack_context(
        "companion",
        800,
        behaviors=[],
        workstreams=[shared],
        matches=[shared, other],
        summaries=[],
        instruments=[],
    )
    assert pack["counts"]["workstream"] == 1
    assert pack["counts"]["match"] == 1
    assert pack["counts"]["matched_total"] == 2
    assert pack["counts"]["dedup"] == 1
    assert pack["fact_ids"] == [10, 11]
