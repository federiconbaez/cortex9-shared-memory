from pathlib import Path

from cortex9_memory.domain.leaks import LIMITS, Thresholds, lint_message, scan
from cortex9_memory.domain.usage_ledger import Burn, Projection, parse_report

FIX = Path(__file__).parent / "fixtures"
TS = "2026-11-26T12:00:00Z"


def _r(name):
    return parse_report((FIX / name).read_text(encoding="utf-8"), TS)


def _rules(findings):
    return {f.rule for f in findings}


def test_real_week_flags_b2b_burst_and_day():
    fs = scan([_r("report_bot_activity.txt"), _r("report_day_bot.txt"), _r("report_routine.txt")])
    rules = _rules(fs)
    assert {"bot_to_bot_total", "bot_to_bot_ratio", "day_peak", "burst_two_days"} <= rules
    b2b = next(f for f in fs if f.rule == "bot_to_bot_total")
    assert b2b.label == "MEDIDO" and b2b.evidence["share_pct"] == 16.0 and b2b.severity == "warn"
    ratio = next(f for f in fs if f.rule == "bot_to_bot_ratio")
    assert ratio.evidence["bot"] == "Ben Tween Forge"
    burst = next(f for f in fs if f.rule == "burst_two_days")
    assert burst.severity == "crit" and burst.evidence["days"] == ["Wed, Nov 25", "Thu, Nov 26"]
    assert "routine_per_run" not in rules  # 1 % en 5 corridas no es fuga
    assert [f.severity for f in fs] == sorted((f.severity for f in fs), key=["crit", "warn", "info"].index)


def test_non_consecutive_days_are_not_a_burst():
    rep = parse_report("by day, as shares:\n- Mon, Nov 23: 30%\n- Wed, Nov 25: 30%\n", TS)
    assert "burst_two_days" not in _rules(scan([rep]))


def test_month_boundary_is_consecutive():
    rep = parse_report("by day, as shares:\n- Mon, Nov 30: 30%\n- Tue, Dec 1: 30%\n", TS)
    assert "burst_two_days" in _rules(scan([rep]))


def test_burn_and_projection_rules():
    burns = [Burn("2026-11-26T03:37:00Z", "2026-11-26T00:26:00Z", "2026-11-26T02:31:00Z", 69.0, 84.0)]
    proj = Projection(84.0, 7.2, 2.2, 1.1, False)
    fs = scan([], burns=burns, projection=proj)
    burn = next(f for f in fs if f.rule == "burn_rate")
    assert burn.severity == "crit" and burn.label == "MEDIDO"
    assert "exhaustion" not in _rules(fs)
    fs2 = scan([], projection=Projection(50.0, 10.0, 5.0, 24.0, True))
    ex = next(f for f in fs2 if f.rule == "exhaustion")
    assert ex.label == "ESTIMADO" and ex.severity == "crit"


def test_routine_and_deleted_rules():
    rep = parse_report("by routine, as shares:\n- Monitor X: 12%, about 7 runs a week\n- Pausada: 9%, paused\n"
                       "- Not from a routine: 79%\n", TS)
    fs = scan([rep])
    rr = [f for f in fs if f.rule == "routine_per_run"]
    assert len(rr) == 1 and rr[0].evidence["routine"] == "Monitor X"
    bots = parse_report("by bot, as shares:\n- Ben: 90%\n- Deleted bots: 7%\n", TS)
    assert "deleted_bots" in _rules(scan([bots]))


def test_thresholds_are_configurable():
    fs = scan([_r("report_bot_activity.txt")], thresholds=Thresholds(b2b_warn=50.0, b2b_crit=60.0))
    assert "bot_to_bot_total" not in _rules(fs)


def test_lint_limits_and_pasted_content():
    ok = lint_message("Hecho: PR #7 verde. Puntero: /workspace/x.md#L1-L20@abc.", "handoff")
    assert ok.ok and ok.tokens <= LIMITS["handoff"]
    long = lint_message("palabra " * 400, "handoff")
    assert not long.ok and any("tokens" in i for i in long.issues)
    block = "Resumen corto\n```\n" + "\n".join(f"line {i}" for i in range(25)) + "\n```\n"
    pasted = lint_message(block, "brief")
    assert not pasted.ok and any("bloque" in i for i in pasted.issues) and pasted.tokens < LIMITS["brief"]
    log = "\n".join(f"2026-11-26T10:{i:02d}:00Z INFO step {i} PASSED" for i in range(16))
    assert any("log" in i for i in lint_message(log, "result").issues)
    rep = "\n".join(["mismo renglon repetido"] * 5)
    assert any("repet" in i for i in lint_message(rep, "user").issues)


def test_lint_unknown_kind():
    import pytest

    from cortex9_memory.domain.models import DomainError

    with pytest.raises(DomainError):
        lint_message("x", "otro")
