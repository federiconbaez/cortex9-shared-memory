import json
import threading

from cortex9_memory.adapters.jsonl_log import JsonlFileLog
from cortex9_memory.application.events import EventService
from cortex9_memory.cli import main


class Clock:
    def now(self):
        return "2026-11-26T01:00:00Z"


def test_concurrent_appends_keep_chain_valid(tmp_path):
    svc = EventService(JsonlFileLog(tmp_path / "e.jsonl"), Clock())

    def w(agent):
        for i in range(20):
            svc.append(agent, "note", "T-12", {"i": i})

    ts = [threading.Thread(target=w, args=(a,)) for a in ("ben", "tween-forge", "federica-fondos")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rep = svc.verify()
    assert rep["ok"] and rep["valid"] == 60


def test_repair_truncates_only_torn_tail(tmp_path):
    path = tmp_path / "e.jsonl"
    svc = EventService(JsonlFileLog(path), Clock())
    svc.append("ben", "note", payload={"a": 1})
    good = path.read_bytes()
    path.write_bytes(good + b'{"kind": "no')  # SIGKILL a mitad de escritura
    assert svc.verify()["ok"] is False
    rep = svc.verify(repair=True)
    assert rep["repaired"] and path.read_bytes() == good
    svc.append("ben", "note", payload={"a": 2})
    assert svc.verify()["valid"] == 2


def test_cli_decide_ledger_and_verify(tmp_path, capsys):
    e = ["--events-log", str(tmp_path / "e.jsonl"), "--agent", "ben"]
    assert main(e + ["decide", "D-1", "Merge por fast-forward con CI verde"]) == 0
    assert main(e + ["contradict", "C-1", "Dos fuentes distintas sobre el deadline"]) == 0
    assert main(e + ["contradict", "C-1", "Dos fuentes distintas sobre el deadline", "--status", "resuelta",
                     "--resolved-by", "D-1"]) == 0
    assert main(e + ["decide", "X-9", "id inválido"]) == 1
    out_md = tmp_path / "DECISIONS.md"
    assert main(e + ["ledger", "--out", str(out_md)]) == 0
    md = out_md.read_text()
    assert "| D-1 |" in md and "| C-1 | resuelta | D-1 |" in md
    capsys.readouterr()
    assert main(e + ["event-verify"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] == 3
    assert not (tmp_path / "data").exists()
