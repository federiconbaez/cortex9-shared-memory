import pytest

from cortex9_memory.domain.budget import (
    UsageSnapshot,
    admit,
    calibrate,
    evaluate,
    level_for,
    predict_delta,
)
from cortex9_memory.domain.models import DomainError

R1 = "2026-11-26T03:37:00Z"  # reset 00:37 ART
R2 = "2026-12-03T03:37:00Z"


def snap(pct, ts="2026-11-25T22:00:00Z", reset=R1, **kw):
    return UsageSnapshot(ts=ts, pct=pct, reset_at=reset, agent=kw.pop("agent", "ben"), **kw)


@pytest.mark.parametrize("pct,level", [(0, "VERDE"), (49.9, "VERDE"), (50, "AMARILLO"), (74.99, "AMARILLO"),
                                       (75, "AMBAR"), (89.9, "AMBAR"), (90, "ROJO"), (100, "ROJO")])
def test_thresholds(pct, level):
    assert level_for(pct) == level


def test_snapshot_validation():
    for bad in (dict(pct=-1), dict(pct=101), dict(pct=10, phase="durante"), dict(pct=10, tokens_est=-5)):
        with pytest.raises(DomainError):
            snap(**bad)
    with pytest.raises(DomainError):
        UsageSnapshot(ts="ayer", pct=1, reset_at=R1, agent="ben")


def test_level_is_monotone_within_window_and_resets_with_new_window():
    now = "2026-11-25T23:00:00Z"
    st = evaluate([snap(79.6), snap(70.0, ts="2026-11-25T22:30:00Z", agent="tween-forge")], now)
    assert st.level == "AMBAR" and st.pct == 79.6
    st2 = evaluate([snap(79.6), snap(91.0, ts="2026-11-25T23:30:00Z")], "2026-11-25T23:40:00Z")
    assert st2.level == "ROJO" and "90" in (st2.alert or "")
    after = evaluate([snap(91.0), snap(1.2, ts="2026-11-26T04:00:00Z", reset=R2)], "2026-11-26T04:10:00Z")
    assert after.level == "VERDE" and after.pct == 1.2 and after.reset_at == R2 and after.alert is None


def test_no_reading_in_active_window_is_unknown():
    st = evaluate([snap(91.0)], "2026-11-26T05:00:00Z")
    assert st.pct is None and st.level == "DESCONOCIDO"
    assert evaluate([], "2026-11-26T05:00:00Z").level == "DESCONOCIDO"


def test_policy_shrinks_with_level():
    now = "2026-11-25T23:00:00Z"
    budgets = [evaluate([snap(p)], now).policy["pack_budget"] for p in (10, 60, 80, 95)]
    assert budgets == sorted(budgets, reverse=True) and budgets[0] == 800 and budgets[-1] == 200


@pytest.mark.parametrize("pct,kind,delta,allowed", [
    (40, "pesada", 8, True),
    (70, "pesada", 8, False),     # cruzaría 75
    (70, "pesada", 4, True),
    (80, "pesada", 1, False),     # ÁMBAR no admite pesadas
    (80, "liviana", 5, True),
    (88, "liviana", 3, False),    # cruzaría 90
    (92, "liviana", 0.5, False),  # ROJO solo cierre
    (92, "cierre", 3, True),
    (99, "cierre", 3, False),     # no pasa de 100
])
def test_admission(pct, kind, delta, allowed):
    st = evaluate([snap(pct)], "2026-11-25T23:00:00Z")
    a = admit(st, kind, delta)
    assert a.allowed is allowed and a.reason


def test_admission_with_unknown_pct_only_light_work():
    st = evaluate([], "2026-11-25T23:00:00Z")
    assert not admit(st, "pesada", 1).allowed
    assert admit(st, "liviana", 1).allowed
    with pytest.raises(DomainError):
        admit(st, "rarísima", 1)


def test_calibration_and_prediction():
    snaps = [
        snap(40.0, task_id="a", phase="before"), snap(47.0, task_id="a", phase="after", tokens_est=70_000),
        snap(47.0, task_id="b", phase="before"), snap(50.0, task_id="b", phase="after", tokens_est=30_000),
        snap(50.0, task_id="c", phase="before"),  # sin after: se ignora
    ]
    rate = calibrate(snaps)
    assert rate == pytest.approx(0.1)  # 0.1 % por cada 1k tokens (mediana)
    assert predict_delta(rate, 20_000) == pytest.approx(2.0)
    assert calibrate([]) is None
    assert predict_delta(None, 1000) is None
