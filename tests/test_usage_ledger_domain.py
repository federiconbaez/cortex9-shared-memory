from pathlib import Path

import pytest

from cortex9_memory.domain.budget import UsageSnapshot
from cortex9_memory.domain.models import DomainError
from cortex9_memory.domain.usage_ledger import burn_rates, parse_report, project_exhaustion

FIX = Path(__file__).parent / "fixtures"
TS = "2026-11-26T12:00:00Z"


def _r(name):
    return parse_report((FIX / name).read_text(encoding="utf-8"), TS)


def test_bot_activity_report_tree():
    r = _r("report_bot_activity.txt")
    assert r.pct_used == 0.6 and r.dims == ("bot", "activity") and r.period == "last_7_days"
    tops = [row for row in r.rows if row.parent is None]
    assert [(t.name, t.share_pct) for t in tops] == [("Ben", 68.0), ("Ben Tween Forge", 31.0), ("Federica Fondos", 1.0)]
    b2b = [row for row in r.rows if row.parent == "Ben Tween Forge" and row.name == "Bot-to-Bot"]
    assert b2b[0].share_pct == 14.0 and b2b[0].level == 1
    approx = [row for row in r.rows if row.approx]
    assert len(approx) == 1 and approx[0].share_pct == 0.5


def test_routine_extras_and_day_dims():
    r = _r("report_routine.txt")
    assert r.dims == ("routine",)
    first = r.rows[0]
    assert first.runs_per_week == 5 and first.paused is False
    assert r.rows[1].paused is True
    d = _r("report_day_bot.txt")
    assert d.dims == ("day", "bot")
    assert [row.name for row in d.rows if row.parent is None][-1] == "Thu, Nov 26"


def test_this_week_period():
    r = parse_report("Grok Bot usage this week (Nov 26, UTC), by bot and activity, as shares of all:\n- Ben: 89%\n", TS)
    assert r.period == "this_week" and r.pct_used is None and r.rows[0].share_pct == 89.0


def test_no_invented_rows():
    with pytest.raises(DomainError):
        parse_report("hola, nada que ver", TS)
    r = parse_report("Weekly usage limit: 12.5% used.\n", TS)
    assert r.rows == () and r.pct_used == 12.5


def test_child_without_parent_is_rejected():
    with pytest.raises(DomainError):
        parse_report("by bot, as shares:\n  - Chats with the user: 3%\n", TS)


def test_round_trip_dict():
    r = _r("report_bot_activity.txt")
    assert type(r).from_dict(r.to_dict()) == r


def _s(ts, pct, reset="2026-11-26T03:37:00Z"):
    return UsageSnapshot(ts=ts, pct=pct, reset_at=reset, agent="ben")


def test_burn_same_window_only_and_no_negative():
    snaps = [
        _s("2026-11-26T00:26:00Z", 69.0),
        _s("2026-11-26T02:17:00Z", 79.6),
        _s("2026-11-26T02:20:00Z", 79.0),  # lectura menor: se ignora el par
        _s("2026-11-26T02:31:00Z", 84.0),
        _s("2026-11-26T12:00:00Z", 0.6, reset="2026-12-03T03:37:00Z"),  # ventana nueva: no cruza
    ]
    burns = burn_rates(snaps)
    assert all(b.pts_per_hour >= 0 for b in burns)
    assert all(b.reset_at == "2026-11-26T03:37:00Z" for b in burns)
    first = burns[0]
    assert first.delta_pct == pytest.approx(10.6) and first.pts_per_hour == pytest.approx(10.6 / (111 / 60), rel=1e-3)
    assert len(burns) == 2


def test_projection():
    p = project_exhaustion(84.0, 7.0, "2026-11-26T02:31:00Z", "2026-11-26T03:37:00Z")
    assert p.hours_to_full == pytest.approx(16 / 7, rel=1e-3) and p.exhausts_before_reset is False
    p2 = project_exhaustion(50.0, 10.0, "2026-11-26T00:00:00Z", "2026-11-27T00:00:00Z")
    assert p2.exhausts_before_reset is True and p2.label == "ESTIMADO"
    assert project_exhaustion(50.0, 0.0, "2026-11-26T00:00:00Z", "2026-11-27T00:00:00Z").hours_to_full is None
