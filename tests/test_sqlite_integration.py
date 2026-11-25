import json
import sqlite3
from pathlib import Path

import pytest

from cortex9_memory.adapters.sqlite_repo import SqliteMemoryRepository
from cortex9_memory.application.service import MemoryService
from cortex9_memory.cli import main
from cortex9_memory.domain.models import DomainError


class FixedClock:
    def __init__(self, t="2026-11-25T10:00:00Z"):
        self.t = t

    def now(self):
        return self.t


@pytest.fixture
def svc(tmp_path):
    repo = SqliteMemoryRepository(tmp_path / "m.sqlite", FixedClock())
    s = MemoryService(repo)
    s.init()
    yield s
    repo.close()


def test_init_idempotent_and_seeds(svc):
    first = svc.repo.stats()
    svc.init()
    assert svc.repo.stats() == first
    assert first["archetypes"] == 4 and first["memory_types"] == 4 and first["confidence_labels"] == 4
    assert {a["slug"] for a in svc.repo.list_archetypes()} == {
        "formalizador-de-lo-informal", "arquitecto-bajo-restriccion",
        "epistemologo-soberano", "constructor-de-instrumentos"}
    assert svc.repo.doctor() == []


def test_fact_fts_search_accents_and_expiry(svc):
    a = svc.add_fact(type="behavior", fact_text="Nunca publicar exploits funcionales", agent="primary", confidence=0.95)
    svc.add_fact(type="workstream", fact_text="Plan de estabilización anclada", source="doc-demo.md")
    old = svc.add_fact(type="world", fact_text="El vault vive en Tasks")
    hits = svc.search("estabilizacion")  # sin tilde → encuentra con tilde
    assert [h["fact_text"] for h in hits] == ["Plan de estabilización anclada"]
    assert svc.search("exploit")[0]["id"] == a  # prefijo
    assert svc.search("exploits", type_="world") == []
    new = svc.add_fact(type="world", fact_text="El vault vive en Tasks v2")
    svc.supersede(old, new)
    ids = [h["id"] for h in svc.search("vault")]
    assert ids == [new]
    assert {h["id"] for h in svc.search("vault", include_expired=True)} == {old, new}
    assert svc.search('"); DROP TABLE x; --') == []  # entrada hostil no rompe FTS
    usage = svc.repo.db.execute("select count(*) from confidence_label_usage").fetchone()[0]
    assert usage == 4


def test_db_level_checks_hold_even_bypassing_domain(svc):
    with pytest.raises(sqlite3.IntegrityError):
        svc.repo.db.execute("insert into memory_facts(type,fact_text,valid_from,created_at) values('otro','x','a','a')")
    with pytest.raises(sqlite3.IntegrityError):
        svc.repo.db.execute("insert into instruments(acronym,name,kind,definition,refutation,created_at) "
                            "values('TX','n','I','d','r','a')")


def test_sessions_summaries(svc):
    sid = svc.start_session("primary", "chat", "Proyecto Demo")
    mid = svc.add_summary(sid, "t269u-t281u", "Tesis reescrita y skill creado", agent="primary")
    assert mid > 0
    with pytest.raises(DomainError):
        svc.add_summary(9999, "t1", "x")


def test_relations_links_and_neighbors(svc):
    iid = svc.add_instrument(acronym="TEME-FNBR", name="Teorema", kind="T", definition="d", refutation="r",
                             related_workstream="tesis")
    rid = svc.relate("instrument", "TEME-FNBR", "instance_of", "archetype", "constructor-de-instrumentos")
    assert rid == svc.relate("instrument", "TEME-FNBR", "instance_of", "archetype", "constructor-de-instrumentos")
    with pytest.raises(DomainError):
        svc.relate("instrument", "NOPE-FNBR", "x", "archetype", "constructor-de-instrumentos")
    if svc.repo.find_id("skill", "investigacion-propia-desde-insumos"):
        svc.link("investigacion-propia-desde-insumos", "instrument", "TEME-FNBR", "producido por")
    n = svc.repo.neighbors("instrument", iid)
    assert any(x["relation"] == "instance_of" for x in n)
    assert svc.repo.doctor() == []


def test_export_import_roundtrip_and_merge(svc, tmp_path):
    svc.add_fact(type="workstream", fact_text="Hecho A")
    payload = svc.export("2026-11-25T10:00:00Z")
    json.dumps(payload)  # serializable
    repo2 = SqliteMemoryRepository(tmp_path / "b.sqlite", FixedClock())
    s2 = MemoryService(repo2)
    repo2.migrate()
    s2.import_(payload, "replace")
    assert repo2.stats() == svc.repo.stats()
    # merge idempotente + fila nueva sin id (como la agregaría Grok)
    payload["tables"]["memory_facts"].append({"type": "world", "fact_text": "Agregado por Grok", "source": "grok"})
    counts = s2.import_(payload, "merge")
    assert counts["memory_facts"] == 1 and counts["archetypes"] == 0
    assert s2.search("grok")[0]["fact_text"] == "Agregado por Grok"
    assert repo2.doctor() == []
    payload["tables"]["memory_facts"].append({"type": "inventado", "fact_text": "x"})
    with pytest.raises(DomainError):
        s2.import_(payload, "merge")
    assert len(s2.search("grok")) == 1  # rollback total
    with pytest.raises(DomainError):
        s2.import_({"format": "otro", "tables": {}})
    repo2.close()


def test_doctor_detects_tampered_migration_and_dangling(svc):
    svc.repo.db.execute("update schema_migrations set checksum='x'")
    svc.repo.db.execute("insert into entity_relations(from_type,from_id,relation,to_type,to_id,created_at) "
                        "values('fact',999,'r','fact',998,'a')")
    svc.repo.db.commit()
    probs = svc.repo.doctor()
    assert any("checksum" in p for p in probs) and any("colgantes" in p for p in probs)


def test_cli_end_to_end(tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    assert main(["--db", db, "init"]) == 0
    assert main(["--db", db, "--agent", "grok", "fact-add", "behavior", "Marcar NO ENCONTRADO en vez de inventar",
                 "--label", "alta", "--meta", '{"origen":"skill"}']) == 0
    capsys.readouterr()
    assert main(["--db", db, "fact-search", "inventar", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["agent"] == "grok" and rows[0]["evidence_label"] == "alta"
    assert main(["--db", db, "fact-add", "world", "x", "--meta", "{bad"]) == 1
    out = tmp_path / "e.json"
    assert main(["--db", db, "export-json", "--out", str(out), "--tables", "archetypes,memory_facts"]) == 0
    data = json.loads(out.read_text())
    assert set(data["tables"]) == {"archetypes", "memory_facts"}
    assert main(["--db", db, "doctor"]) == 0


def test_source_key_idempotent_upsert(svc):
    id1 = svc.add_fact(type="workstream", fact_text="Estado v1", source_key="ws:alpha", agent="primary")
    id2 = svc.add_fact(type="workstream", fact_text="Estado v2", source_key="ws:alpha", agent="companion", confidence=0.95)
    assert id1 == id2
    rows = svc.search("Estado")
    assert len(rows) == 1
    assert rows[0]["fact_text"] == "Estado v2"
    assert rows[0]["agent"] == "companion"


def test_point_in_time_facts_and_touch_decay(tmp_path):
    clk = FixedClock("2026-11-01T00:00:00Z")
    repo = SqliteMemoryRepository(tmp_path / "t.sqlite", clk)
    s = MemoryService(repo)
    s.init()
    old_id = s.add_fact(type="world", fact_text="Regla histórica vigente en noviembre 1", valid_from="2026-11-01T00:00:00Z")
    clk.t = "2026-11-15T00:00:00Z"
    new_id = s.add_fact(type="world", fact_text="Regla nueva vigente desde noviembre 15", valid_from="2026-11-15T00:00:00Z")
    s.supersede(old_id, new_id)

    # Hoy (nov 15) solo aparece la nueva; el 10 de noviembre solo existía la vieja.
    assert [r["id"] for r in s.search("Regla")] == [new_id]
    assert [r["id"] for r in s.search("Regla", as_of="2026-11-10T00:00:00Z")] == [old_id]

    # Touch incrementa access_count.
    s.search("Regla", touch=True)
    row = repo.db.execute("SELECT access_count, last_accessed_at FROM memory_facts WHERE id=?", (new_id,)).fetchone()
    assert row["access_count"] == 1 and row["last_accessed_at"] == "2026-11-15T00:00:00Z"
    repo.close()


def test_temporal_relations_as_of_and_invalidate(tmp_path):
    clk = FixedClock("2026-11-01T00:00:00Z")
    repo = SqliteMemoryRepository(tmp_path / "rel.sqlite", clk)
    s = MemoryService(repo)
    s.init()
    f1 = s.add_fact(type="world", fact_text="Entidad origen")
    f2 = s.add_fact(type="world", fact_text="Entidad destino")
    rel_id = s.relate("fact", str(f1), "supports", "fact", str(f2), valid_from="2026-11-01T00:00:00Z")
    assert len(repo.neighbors("fact", f1, as_of="2026-11-05T00:00:00Z")) == 1
    clk.t = "2026-11-10T00:00:00Z"
    s.invalidate_relation(rel_id)
    assert len(repo.neighbors("fact", f1)) == 0
    assert len(repo.neighbors("fact", f1, as_of="2026-11-05T00:00:00Z")) == 1
    assert len(repo.neighbors("fact", f1, include_expired=True)) == 1
    repo.close()


def test_skill_sync_readonly_and_context_pack(tmp_path):
    wf = tmp_path / "workflows"
    (wf / "mi-protocolo").mkdir(parents=True)
    (wf / "mi-protocolo" / "SKILL.md").write_text(
        "---\nname: Mi Protocolo\ndescription: demo\n---\n# Ignorado\nCuerpo.\n", encoding="utf-8"
    )
    repo = SqliteMemoryRepository(tmp_path / "s.sqlite", FixedClock())
    s = MemoryService(repo)
    s.init()
    synced = s.sync_skills(str(wf), link_archetypes=True)
    assert len(synced) == 1 and synced[0]["slug"] == "mi-protocolo" and synced[0]["title"] == "Mi Protocolo"

    s.add_fact(type="behavior", fact_text="Siempre verificar fuentes primarias", evidence_label="alta")
    s.add_fact(type="workstream", fact_text="Sprint de pruebas en curso", evidence_label="alta")
    sid = s.start_session("companion", "chat", "Prueba")
    s.add_summary(sid, "t1-t3", "Resumen inicial de la sesión", agent="companion")
    pack = s.context_pack("companion", budget_tokens=400, query="pruebas")
    assert pack["tokens_est"] <= 400
    assert "Siempre verificar fuentes primarias" in pack["text"]
    assert "Sprint de pruebas en curso" in pack["text"]
    assert "Resumen inicial" in pack["text"]
    assert pack["counts"]["matched_total"] >= 1
    # Por defecto no incrementa access_count
    rows = repo.db.execute("SELECT access_count FROM memory_facts").fetchall()
    assert all(r["access_count"] == 0 for r in rows)
    repo.close()


def test_backup_and_rotation(tmp_path):
    clk = FixedClock("2026-11-25T10:00:00Z")
    repo = SqliteMemoryRepository(tmp_path / "live.sqlite", clk)
    s = MemoryService(repo)
    s.init()
    s.add_fact(type="world", fact_text="Dato persistido")
    bdir = tmp_path / "baks"
    for sec in ("01", "02", "03"):
        res = s.backup(str(bdir), f"2026-11-25T10:00:{sec}Z", keep=2)
    assert len(list(bdir.glob("c9mem-*.sqlite"))) == 2
    assert len(res["removed"]) == 1
    assert res["integrity"] == "ok"
    chk = sqlite3.connect(res["backup"])
    assert chk.execute("SELECT count(*) FROM memory_facts").fetchone()[0] == 1
    chk.close()
    repo.close()



def test_context_pack_default_does_not_touch_and_touch_once(tmp_path):
    repo = SqliteMemoryRepository(tmp_path / "touch.sqlite", FixedClock())
    s = MemoryService(repo)
    s.init()
    fid = s.add_fact(
        type="workstream",
        fact_text="Verificación de agente par en memoria compartida",
        evidence_label="alta",
        source_key="smoke:shared",
    )
    pack = s.context_pack("companion", budget_tokens=500, query="agente par")
    acc = repo.db.execute("SELECT access_count FROM memory_facts WHERE id=?", (fid,)).fetchone()[0]
    assert acc == 0
    assert pack["counts"]["workstream"] == 1
    assert pack["counts"]["match"] == 0
    assert pack["counts"]["matched_total"] >= 1
    assert pack["counts"]["dedup"] >= 1

    pack2 = s.context_pack("companion", budget_tokens=500, query="agente par", touch=True)
    acc = repo.db.execute("SELECT access_count FROM memory_facts WHERE id=?", (fid,)).fetchone()[0]
    assert acc == 1
    assert pack2["counts"]["dedup"] >= 1
    repo.close()


def test_backup_json_reports_integrity_error(tmp_path):
    clk = FixedClock("2026-11-25T10:00:00Z")
    repo = SqliteMemoryRepository(tmp_path / "live.sqlite", clk)
    s = MemoryService(repo)
    s.init()

    def broken_backup(dest_path: str):
        Path(dest_path).write_bytes(b"not-a-db")
        return dest_path, "file is not a database"

    repo.backup_to = broken_backup  # type: ignore[method-assign]
    res = s.backup(str(tmp_path / "baks"), "2026-11-25T10:00:00Z", keep=2)
    assert res["integrity"] == "file is not a database"
    assert res["backup"].endswith("c9mem-20261125-100000Z.sqlite")
    repo.close()
