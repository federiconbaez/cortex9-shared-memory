import json

from cortex9_memory.adapters.sqlite_repo import SqliteMemoryRepository
from cortex9_memory.application.service import MemoryService
from cortex9_memory.cli import main


class Clock:
    def now(self):
        return "2026-11-25T10:00:00Z"


def _svc(tmp_path):
    repo = SqliteMemoryRepository(tmp_path / "m.sqlite", Clock())
    s = MemoryService(repo)
    s.init()
    return s


def test_knapsack_is_default_and_replayable(tmp_path):
    s = _svc(tmp_path)
    for i in range(12):
        s.add_fact(type="workstream", fact_text=f"Repo número {i} publicado con CI verde " + "x" * (i * 9))
    s.add_fact(type="behavior", fact_text="Nombre sin tildes en commits", trust_tier="T3")
    a = s.context_pack("ben", 160, query="repo")
    b = s.context_pack("ben", 160, query="repo")
    g = s.context_pack("ben", 160, query="repo", strategy="greedy")
    assert a["strategy"] == "knapsack" and a["pack_sha256"] == b["pack_sha256"]
    assert a["tokens_est"] <= 160 and g["tokens_est"] <= 160
    assert "Nombre sin tildes" in a["text"]


def test_cli_strategy_flag(tmp_path, capsys):
    db = ["--db", str(tmp_path / "c.sqlite")]
    assert main(db + ["init"]) == 0
    assert main(db + ["fact-add", "workstream", "Frente demo"]) == 0
    capsys.readouterr()
    assert main(db + ["context-pack", "--budget", "200", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["strategy"] == "knapsack" and len(out["pack_sha256"]) == 64
    assert main(db + ["context-pack", "--budget", "200", "--strategy", "greedy", "--json"]) == 0
    assert "strategy" not in json.loads(capsys.readouterr().out)
