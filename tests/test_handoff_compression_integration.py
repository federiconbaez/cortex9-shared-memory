import json

import pytest

from cortex9_memory.adapters.hasher_fs import FsFileHasher
from cortex9_memory.adapters.jsonl_log import JsonlFileLog
from cortex9_memory.adapters.sqlite_repo import SqliteMemoryRepository
from cortex9_memory.application.budget import BudgetService
from cortex9_memory.application.events import EventService
from cortex9_memory.application.handoff import HandoffService
from cortex9_memory.application.service import MemoryService
from cortex9_memory.cli import main
from cortex9_memory.domain.models import DomainError


class Clock:
    def now(self):
        return "2026-11-25T23:00:00Z"


@pytest.fixture
def svc(tmp_path):
    repo = SqliteMemoryRepository(tmp_path / "m.sqlite", Clock())
    s = MemoryService(repo)
    s.init()
    yield s
    repo.close()


def test_codebook_persists_and_roundtrips(svc):
    svc.codebook_add("R1", "/home/box/cortex9-memory", "ben")
    svc.codebook_add("R1", "/home/box/cortex9-memory", "ben")  # idempotente
    with pytest.raises(DomainError):
        svc.codebook_add("R1", "/otra/ruta/larga", "ben")
    with pytest.raises(DomainError):
        svc.codebook_add("R2", "/home/box/cortex9-memory", "ben")  # expansión única
    text = "ver /home/box/cortex9-memory/README.md y @{R1} literal"
    assert svc.decode(svc.encode(text)) == text
    assert svc.repo.doctor() == []


def test_summary_rejected_when_probe_fails(svc):
    sid = svc.start_session("primary", "chat", "x")
    src = "PR #5 mergeado en 1e31b05, decisión D-1, uso 81.2%"
    with pytest.raises(DomainError, match="sonda"):
        svc.add_summary(sid, "t1-t5", "Se mergeó un PR.", source_text=src)
    assert svc.add_summary(sid, "t1-t5", "D-1; 1e31b05; 81.2%", source_text=src) > 0


def _hs(tmp_path, pct=None):
    budget = BudgetService(JsonlFileLog(tmp_path / "u.jsonl"), Clock())
    if pct is not None:
        budget.record(pct, "2026-11-26T03:37:00Z", "ben")
    events = EventService(JsonlFileLog(tmp_path / "e.jsonl"), Clock())
    return HandoffService(FsFileHasher(), budget, events), events


def _raw(tmp_path):
    f = tmp_path / "DISENO.md"
    f.write_text("diseño v1", encoding="utf-8")
    return {"id": "HO-20261126-01", "from": "ben", "to": "tween-forge", "task_id": "T-12",
            "goal": "Implementar PR6", "inputs": [f"{f}#L1-L1"],
            "budget_guard": {"max_delta_pct": 3, "stop_if_weekly_pct_ge": 75}}, f


def test_prepare_pins_hashes_logs_event_and_detects_staleness(tmp_path):
    hs, events = _hs(tmp_path, pct=40)
    raw, f = _raw(tmp_path)
    out = hs.prepare(raw)
    assert "@" in out["envelope"]["inputs"][0] and out["budget"]["level"] == "VERDE"
    assert hs.check(out["envelope"])["ok"]
    f.write_text("diseño v2", encoding="utf-8")
    assert hs.check(out["envelope"])["stale_inputs"] == [str(f)]
    assert events.verify()["valid"] == 1
    res = hs.result({"id": "HO-20261126-01", "from": "tween-forge", "status": "done", "summary": "listo",
                     "artifacts": [str(f)]})
    assert res["envelope"]["artifacts"][0].startswith(str(f) + "@")
    assert events.verify()["valid"] == 2


@pytest.mark.parametrize("pct", [76.0, 73.0])
def test_prepare_aborts_on_budget(tmp_path, pct):
    hs, _ = _hs(tmp_path, pct=pct)
    raw, _ = _raw(tmp_path)
    with pytest.raises(DomainError, match="aborted_budget"):
        hs.prepare(raw)


def test_cli_fold_probe_codebook_and_handoff(tmp_path, capsys):
    db = ["--db", str(tmp_path / "c.sqlite"), "--usage-log", str(tmp_path / "u.jsonl"),
          "--events-log", str(tmp_path / "e.jsonl"), "--agent", "ben"]
    log = tmp_path / "pytest.log"
    log.write_text("\n".join(["x PASSED"] * 200 + ["FAILED t::a - E", "== 1 failed, 200 passed in 1s =="]))
    assert main(db + ["fold", "--file", str(log)]) == 0
    out = capsys.readouterr().out
    assert "FAILED t::a" in out and "1 failed, 200 passed" in out and len(out) < 600
    src, summ = tmp_path / "s.txt", tmp_path / "r.txt"
    src.write_text("HEAD 1e31b05 y D-4")
    summ.write_text("Se hizo algo")
    assert main(db + ["summary-probe", "--source", str(src), "--summary", str(summ)]) == 8
    assert main(db + ["codebook-add", "BM", "/workspace/bot-memory"]) == 0
    capsys.readouterr()
    assert main(db + ["encode", "ver /workspace/bot-memory/x"]) == 0
    assert capsys.readouterr().out.strip() == "ver @{BM}/x"
    raw, _ = _raw(tmp_path)
    hf = tmp_path / "ho.json"
    hf.write_text(json.dumps(raw))
    assert main(db + ["handoff-prepare", "--file", str(hf)]) == 0
    env = json.loads(capsys.readouterr().out)
    hf.write_text(json.dumps(env))
    assert main(db + ["handoff-check", "--file", str(hf)]) == 0
    assert main(db + ["usage-record", "--pct", "80", "--reset-at", "2099-01-01T00:00:00Z"]) == 0
    hf.write_text(json.dumps(raw))
    assert main(db + ["handoff-prepare", "--file", str(hf)]) == 9
