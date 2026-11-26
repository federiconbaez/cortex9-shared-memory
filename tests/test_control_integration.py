import json
import threading

from cortex9_memory.adapters.jsonl_log import JsonlFileLog
from cortex9_memory.adapters.lease_fs import FsLeaseStore
from cortex9_memory.application.budget import BudgetService
from cortex9_memory.application.control import ControlService
from cortex9_memory.application.usage_ledger import LedgerService
from cortex9_memory.cli import main

RESET = "2099-12-03T03:37:00Z"


class Clock:
    def __init__(self, t="2099-11-26T13:00:00Z"):
        self.t = t

    def now(self):
        return self.t


def _svc(tmp_path, clock):
    b = BudgetService(JsonlFileLog(tmp_path / "u.jsonl"), clock)
    return ControlService(b, LedgerService(JsonlFileLog(tmp_path / "s.jsonl"), b, clock),
                          JsonlFileLog(tmp_path / "t.jsonl"), FsLeaseStore(tmp_path / "heavy.lease"), clock)


def test_mutex_between_bots_and_task_accounting(tmp_path):
    clock = Clock()
    svc = _svc(tmp_path, clock)
    v = svc.guard("pesada", "ben", "T-13", 3.0, take=True, pct=10.0, reset_at=RESET)
    assert v.decision == "ADMITIR"
    w = svc.guard("pesada", "tween-forge", "MCP-4", 2.0, take=True)
    assert w.decision == "ESPERAR" and "T-13" in w.reasons[-1]
    assert svc.guard("liviana", "tween-forge", "doc-1", 0.5).decision == "ADMITIR"  # liviano no bloquea
    clock.t = "2099-11-26T14:00:00Z"
    out = svc.task_end("T-13", "ben", 12.0)
    assert out.delta == 2.0 and not out.anomaly
    assert svc.lease_status()["held"] is False
    assert svc.guard("pesada", "tween-forge", "MCP-4", 2.0, take=True).decision == "ADMITIR"
    phases = [json.loads(x)["phase"] for x in (tmp_path / "u.jsonl").read_text().splitlines()]
    assert phases.count("before") == 2 and phases.count("after") == 1


def test_concurrent_acquire_grants_exactly_one(tmp_path):
    clock = Clock()
    svc = _svc(tmp_path, clock)
    svc.budget.record(10.0, RESET, "ben")
    results = []

    def go(agent):
        results.append(_svc(tmp_path, clock).guard("pesada", agent, f"T-{agent}", 0.5, take=True).decision)

    ts = [threading.Thread(target=go, args=(f"bot-{i}",)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert results.count("ADMITIR") == 1 and results.count("ESPERAR") == 7


def test_anomaly_and_release_rules(tmp_path):
    clock = Clock()
    svc = _svc(tmp_path, clock)
    svc.guard("pesada", "ben", "T-1", 1.0, take=True, pct=5.0, reset_at=RESET)
    assert svc.lease_release("tween-forge", "X", False)["released"] is False
    clock.t = "2099-11-26T13:30:00Z"
    assert svc.task_end("T-1", "ben", 9.0).anomaly is True


def test_guard_cli_exit_codes(tmp_path, capsys):
    base = ["--usage-log", str(tmp_path / "u.jsonl"), "--shares-log", str(tmp_path / "s.jsonl"),
            "--tasks-log", str(tmp_path / "t.jsonl"), "--lease-file", str(tmp_path / "l"), "--agent", "ben"]
    assert main(base + ["guard", "--kind", "pesada", "--task", "A", "--delta", "2"]) == 10  # sin lectura
    capsys.readouterr()
    rc = main(base + ["guard", "--kind", "pesada", "--task", "A", "--delta", "2", "--pct", "84",
                      "--reset-at", "2099-12-03T00:37:00-03:00", "--short"])
    assert rc == 11 and capsys.readouterr().out.startswith("DENEGAR")
    assert main(base + ["guard", "--kind", "cierre", "--task", "B", "--delta", "1"]) == 0
    pol = tmp_path / "p.json"
    pol.write_text(json.dumps({"reserve_pct": 10}))
    capsys.readouterr()
    assert main(base + ["--policy", str(pol), "guard", "--kind", "liviana", "--task", "C", "--delta", "1"]) in (0, 10)
    assert main(base + ["task-end", "--task", "nada", "--pct", "1"]) == 1


def test_recent_crit_report_holds_heavy_until_ack(tmp_path):
    from pathlib import Path

    clock = Clock("2026-11-26T13:00:00Z")
    svc = _svc(tmp_path, clock)
    svc.ledger.ingest((Path(__file__).parent / "fixtures" / "report_day_bot.txt").read_text(), "ben")
    svc.budget.record(1.0, "2026-12-03T03:37:00Z", "ben")
    v = svc.guard("pesada", "ben", "T-2", 1.0)
    assert v.decision == "ESPERAR" and "crítico" in v.reasons[0]
    assert svc.guard("pesada", "ben", "T-2", 1.0, ack=True).decision == "ADMITIR"
    clock.t = "2026-11-27T14:00:00Z"  # reporte de más de 24 h: ya no frena
    svc.budget.record(1.5, "2026-12-03T03:37:00Z", "ben")
    assert svc.guard("pesada", "ben", "T-2", 1.0).decision == "ADMITIR"
