import json
from pathlib import Path

from cortex9_memory.cli import main

FIX = Path(__file__).parent / "fixtures"


def _args(tmp_path):
    return ["--usage-log", str(tmp_path / "u.jsonl"), "--shares-log", str(tmp_path / "s.jsonl"), "--agent", "ben"]


def test_ingest_feeds_ledger_and_budget(tmp_path, capsys):
    rc = main(_args(tmp_path) + ["usage-ingest", "--file", str(FIX / "report_bot_activity.txt"),
                                 "--reset-at", "2099-12-03T00:37:00-03:00"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["rows"] == 10 and out["budget_recorded"] is True
    line = json.loads((tmp_path / "s.jsonl").read_text().splitlines()[0])
    assert line["agent"] == "ben" and line["dims"] == ["bot", "activity"]
    snap = json.loads((tmp_path / "u.jsonl").read_text().splitlines()[0])
    assert snap["pct"] == 0.6 and snap["source"] == "report" and snap["reset_at"] == "2099-12-03T03:37:00Z"


def test_burn_over_cli(tmp_path, capsys):
    for ts, pct in (("2099-11-26T00:26:00Z", 69.0), ("2099-11-26T02:31:00Z", 84.0)):
        main(_args(tmp_path) + ["usage-record", "--pct", str(pct), "--reset-at", "2099-12-26T03:37:00Z"])
        # fija ts determinista reescribiendo la última línea
        p = tmp_path / "u.jsonl"
        lines = p.read_text().splitlines()
        d = json.loads(lines[-1])
        d["ts"] = ts
        lines[-1] = json.dumps(d)
        p.write_text("\n".join(lines) + "\n")
    capsys.readouterr()
    assert main(_args(tmp_path) + ["usage-burn"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["burns"][0]["label"] == "MEDIDO" and out["burns"][0]["pts_per_hour"] == 7.2
    assert out["projection"]["label"] == "ESTIMADO"


def test_garbage_report_is_rejected(tmp_path, capsys):
    f = tmp_path / "x.txt"
    f.write_text("sin datos")
    assert main(_args(tmp_path) + ["usage-ingest", "--file", str(f)]) == 1
    assert not (tmp_path / "s.jsonl").exists() or (tmp_path / "s.jsonl").read_text() == ""
