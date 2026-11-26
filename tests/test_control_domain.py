import itertools

import pytest

from cortex9_memory.domain.budget import BudgetStatus, UsageSnapshot, evaluate
from cortex9_memory.domain.guard import ADMITIR, DENEGAR, ESPERAR, GuardInput, decide, recommend_effort
from cortex9_memory.domain.lease import Lease, acquire, blocker, release
from cortex9_memory.domain.models import DomainError
from cortex9_memory.domain.splits import SplitPolicy, allocate, day_start_pct, days_left
from cortex9_memory.domain.tasks import TaskStart, close_task, spent_today

RESET = "2026-12-03T03:37:00Z"  # jueves 3/12 00:37 ART
NOW = "2026-11-26T13:00:00Z"  # jueves 26/11 10:00 ART
P = SplitPolicy()


def _snap(ts, pct, reset=RESET):
    return UsageSnapshot(ts=ts, pct=pct, reset_at=reset, agent="ben")


# ---------- splits ----------

def test_days_left_counts_local_days():
    assert days_left(NOW, RESET, -180) == 7
    assert days_left("2026-12-03T03:10:00Z", RESET, -180) == 1  # 00:10 ART del día del reset
    assert days_left("2026-12-02T23:00:00Z", RESET, -180) == 1


def test_allocate_week_day_bot_task():
    a = allocate(P, 10.0, 4.0, NOW, RESET, "ben", bot_spent_today=2.0)
    assert a.cap == 85.0 and a.days_left == 7
    assert a.day_budget == pytest.approx((85 - 4) / 7)
    assert a.day_spent == pytest.approx(6.0) and a.day_left == pytest.approx(81 / 7 - 6)
    assert a.bot_share == pytest.approx(0.6) and a.bot_left == pytest.approx(81 / 7 * 0.6 - 2)
    assert a.task_cap == pytest.approx(min(8.0, a.bot_left))
    assert a.reserve_headroom == 75.0


def test_unknown_agent_gets_default_weight_normalized():
    assert P.share("otro-bot") == pytest.approx(0.1 / 1.1)
    assert sum(P.share(b) for b in P.weights) == pytest.approx(1.0)


def test_dynamic_pacing_gives_more_when_underspent():
    lo = allocate(P, 5.0, 5.0, NOW, RESET, "ben")
    hi = allocate(P, 40.0, 40.0, NOW, RESET, "ben")
    assert lo.day_budget > hi.day_budget
    assert allocate(P, 90.0, 90.0, NOW, RESET, "ben").day_budget == 0.0


def test_day_start_pct_uses_previous_days_max():
    snaps = [_snap("2026-11-26T02:00:00Z", 3.0),  # 25/11 23:00 ART (ayer local)
             _snap("2026-11-26T12:00:00Z", 6.0), _snap("2026-11-26T12:30:00Z", 7.0)]
    assert day_start_pct(snaps, NOW, RESET, -180) == 3.0
    assert day_start_pct(snaps[1:], NOW, RESET, -180) == 6.0
    assert day_start_pct([], NOW, RESET, -180) is None


def test_policy_validation_and_from_dict():
    with pytest.raises(DomainError):
        SplitPolicy(reserve_pct=100)
    with pytest.raises(DomainError):
        SplitPolicy.from_dict({"reserva": 10})
    assert SplitPolicy.from_dict({"reserve_pct": 20}).cap == 80


# ---------- lease ----------

def test_lease_mutex_ttl_and_owner():
    got, b = acquire(None, "ben", "T-1", "pesada", NOW, 90)
    assert got and b is None and got.expires_at == "2026-11-26T14:30:00Z"
    got2, b2 = acquire(got, "tween-forge", "T-2", "pesada", "2026-11-26T13:30:00Z", 90)
    assert got2 is None and b2 == got
    renewed, _ = acquire(got, "ben", "T-1", "pesada", "2026-11-26T14:00:00Z", 90)
    assert renewed and renewed.acquired_at == NOW and renewed.expires_at == "2026-11-26T15:30:00Z"
    freed, _ = acquire(got, "tween-forge", "T-2", "pesada", "2026-11-26T14:30:00Z", 90)  # vencido = libre
    assert freed and freed.agent == "tween-forge"
    assert release(got, "tween-forge", "T-2", NOW) == (False, "en manos de ben/T-1")
    assert release(got, "tween-forge", "T-2", NOW, force=True)[0]
    assert blocker(got, "ben", "T-1", NOW) is None
    assert Lease.from_json("{roto") is None and Lease.from_json(got.to_json()) == got


# ---------- tasks ----------

def test_close_task_delta_and_anomaly():
    s = TaskStart("T-9", "ben", "pesada", 3.0, 10.0, NOW, RESET)
    ok = close_task(s, 12.5, "2026-11-26T14:00:00Z", RESET, P)
    assert ok.delta == 2.5 and not ok.anomaly
    bad = close_task(s, 17.0, "2026-11-26T14:00:00Z", RESET, P)
    assert bad.anomaly and bad.delta == 7.0
    neg = close_task(s, 9.0, "2026-11-26T14:00:00Z", RESET, P)
    assert neg.delta == 0.0 and neg.warnings
    crossed = close_task(s, 1.0, "2026-12-03T04:00:00Z", "2026-12-10T03:37:00Z", P)
    assert crossed.delta == 0.0 and "reset" in crossed.warnings[0]
    with pytest.raises(DomainError):
        close_task(s, 11.0, "2026-11-26T12:00:00Z", RESET, P)
    assert spent_today([ok, bad], "ben", "2026-11-26T20:00:00Z", RESET, -180) == 9.5
    assert spent_today([ok], "ben", "2026-11-27T13:00:00Z", RESET, -180) == 0.0


# ---------- guard ----------

def _st(pct, now=NOW):
    return evaluate([_snap("2026-11-26T12:50:00Z", pct)], now) if pct is not None else BudgetStatus(
        "DESCONOCIDO", None, None)


def _g(kind="pesada", delta=2.0, pct=10.0, **kw):
    st = _st(pct)
    alloc = allocate(P, pct, pct - 1.0, NOW, RESET, kw.pop("agent", "ben"), kw.pop("spent", 0.0)) \
        if pct is not None else None
    base = {"kind": kind, "agent": "ben", "task_id": "T-1", "delta": delta, "now": NOW, "status": st,
            "last_reading_ts": "2026-11-26T12:50:00Z" if pct is not None else None, "alloc": alloc, "policy": P}
    base.update(kw)
    return GuardInput(**base)


def test_admit_happy_path_and_effort():
    v = decide(_g())
    assert v.decision == ADMITIR and v.exit_code == 0 and v.effort == "low"
    assert decide(_g(needs="diseno")).effort == "normal"
    assert recommend_effort("AMBAR", "diseno") == "low"


def test_wait_reasons():
    assert decide(_g(pct=None)).decision == ESPERAR
    assert decide(_g(kind="liviana", pct=None)).decision == ADMITIR
    assert decide(_g(burn_pph=5.0)).decision == ESPERAR
    assert decide(_g(kind="liviana", burn_pph=5.0)).decision == ADMITIR
    assert decide(_g(kind="liviana", burn_pph=7.5)).decision == ESPERAR
    assert decide(_g(crit_findings=1)).decision == ESPERAR
    assert decide(_g(crit_findings=1, ack=True)).decision == ADMITIR
    lb = Lease("tween-forge", "MCP-3", "pesada", NOW, "2026-11-26T14:00:00Z")
    v = decide(_g(lease_blocker=lb))
    assert v.decision == ESPERAR and "MCP-3" in v.reasons[0] and v.exit_code == 10
    assert decide(_g(delta=7.5)).decision == ESPERAR  # cuota del bot (≈6.9) agotada
    stale = _g(last_reading_ts="2026-11-26T09:00:00Z")
    assert decide(stale).decision == ESPERAR


def test_deny_reasons_and_priority():
    assert decide(_g(pct=95.0, kind="liviana")).decision == DENEGAR
    v = decide(_g(pct=84.0, delta=2.0))
    assert v.decision == DENEGAR and "reserva" in v.reasons[0] and v.exit_code == 11
    assert decide(_g(pct=84.0, delta=2.0, emergency=True, ack=True)).decision != DENEGAR
    assert decide(_g(delta=9.0)).decision == DENEGAR
    assert decide(_g(pct=78.0, delta=1.0)).decision == ESPERAR
    assert decide(_g(pct=78.0, delta=1.0, ack=True)).decision in (ADMITIR, ESPERAR)


def test_cierre_never_denied_and_deterministic():
    for pct, burn, crit in itertools.product((None, 10.0, 80.0, 95.0), (None, 9.0), (0, 2)):
        g = _g(kind="cierre", pct=pct, burn_pph=burn, crit_findings=crit, delta=50.0)
        assert decide(g).decision == ADMITIR
    assert decide(_g()) == decide(_g())


def test_guard_input_validation():
    with pytest.raises(DomainError):
        _g(kind="otra")
    with pytest.raises(DomainError):
        _g(needs="ganas")
