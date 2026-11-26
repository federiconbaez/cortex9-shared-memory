import json
import threading

from cortex9_memory.adapters.jsonl_log import JsonlFileLog
from cortex9_memory.application.budget import BudgetService
from cortex9_memory.cli import main


class Clock:
    t = "2026-11-25T23:00:00Z"

    def now(self):
        return self.t


def test_record_status_and_torn_line_tolerance(tmp_path):
    log = JsonlFileLog(tmp_path / "u.jsonl")
    svc = BudgetService(log, Clock())
    svc.record(79.6, "2026-11-26T03:37:00Z", "ben")
    (tmp_path / "u.jsonl").open("a").write('{"ts": "2026-11-25T22:59')  # cola rota de otra bot
    st = svc.record(81.0, "2026-11-26T03:37:00Z", "tween-forge")
    assert st.level == "AMBAR" and st.pct == 81.0 and st.readings == 2
    snaps, bad = svc.snapshots()
    assert len(snaps) == 2 and bad == 1


def test_concurrent_appends_keep_every_line(tmp_path):
    svc = BudgetService(JsonlFileLog(tmp_path / "u.jsonl"), Clock())

    def w(agent):
        for i in range(25):
            svc.record(10 + i, "2026-11-26T03:37:00Z", agent)

    ts = [threading.Thread(target=w, args=(a,)) for a in ("ben", "tween-forge", "federica-fondos")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    snaps, bad = svc.snapshots()
    assert len(snaps) == 75 and bad == 0


def test_admit_uses_calibration(tmp_path):
    svc = BudgetService(JsonlFileLog(tmp_path / "u.jsonl"), Clock())
    svc.record(40, "2026-11-26T03:37:00Z", "ben", task_id="r1", phase="before", ts="2026-11-25T20:00:00Z")
    svc.record(47, "2026-11-26T03:37:00Z", "ben", task_id="r1", phase="after", tokens_est=70_000,
               ts="2026-11-25T21:00:00Z")
    res = svc.admit("pesada", tokens_est=300_000)
    assert res["delta_source"] == "calibrado" and res["est_delta_pct"] == 30.0 and res["allowed"] is False
    assert svc.admit("pesada", tokens_est=200_000)["allowed"] is True


def test_cli_usage(tmp_path, capsys):
    u = ["--usage-log", str(tmp_path / "u.jsonl"), "--agent", "ben"]
    assert main(u + ["usage-record", "--pct", "79.6", "--reset-at", "2099-11-26T00:37:00-03:00"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["level"] == "AMBAR" and out["reset_at"] == "2099-11-26T03:37:00Z" and "75" in out["alert"]
    assert main(u + ["usage-admit", "--kind", "pesada", "--delta", "1"]) == 6
    assert main(u + ["usage-admit", "--kind", "liviana", "--delta", "1"]) == 0
    assert main(u + ["usage-admit", "--kind", "liviana"]) == 1
    assert not (tmp_path / "data").exists()
