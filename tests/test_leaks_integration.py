import json
from pathlib import Path

from cortex9_memory.cli import main

FIX = Path(__file__).parent / "fixtures"


def _args(tmp_path):
    return ["--usage-log", str(tmp_path / "u.jsonl"), "--shares-log", str(tmp_path / "s.jsonl"), "--agent", "ben"]


def test_leak_scan_on_real_week(tmp_path, capsys):
    for f in ("report_bot_activity.txt", "report_day_bot.txt", "report_routine.txt"):
        assert main(_args(tmp_path) + ["usage-ingest", "--file", str(FIX / f)]) == 0
    capsys.readouterr()
    rc = main(_args(tmp_path) + ["leak-scan"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 7 and out["crit"] >= 1
    assert {f["rule"] for f in out["findings"]} >= {"bot_to_bot_total", "burst_two_days"}
    assert all(f["label"] in ("MEDIDO", "ESTIMADO") for f in out["findings"])


def test_leak_scan_latest_report_wins(tmp_path, capsys):
    a = tmp_path / "a.txt"
    a.write_text("usage over the last 7 days, by bot and activity, as shares:\n- Ben: 50%\n  - Bot-to-Bot: 30%\n")
    b = tmp_path / "b.txt"
    b.write_text("usage over the last 7 days, by bot and activity, as shares:\n- Ben: 100%\n  - Bot-to-Bot: 1%\n")
    main(_args(tmp_path) + ["usage-ingest", "--file", str(a)])
    main(_args(tmp_path) + ["usage-ingest", "--file", str(b)])
    capsys.readouterr()
    assert main(_args(tmp_path) + ["leak-scan"]) == 0
    assert json.loads(capsys.readouterr().out)["findings"] == []


def test_msg_lint_cli(tmp_path, capsys):
    f = tmp_path / "m.txt"
    f.write_text("PR #7 verde; puntero /workspace/x.md#L1-L9@ab12")
    assert main(["msg-lint", "--kind", "handoff", "--file", str(f)]) == 0
    f.write_text("x " * 2000)
    capsys.readouterr()
    assert main(["msg-lint", "--kind", "handoff", "--file", str(f)]) == 8
    assert json.loads(capsys.readouterr().out)["ok"] is False
