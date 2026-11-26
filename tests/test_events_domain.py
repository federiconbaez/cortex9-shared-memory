import itertools
import json

import pytest

from cortex9_memory.domain.events import (
    GENESIS,
    build_next,
    fold_ledger,
    merge_ledgers,
    merkle_root,
    render_ledger,
    verify,
)
from cortex9_memory.domain.models import DomainError


def chain(n, kind="note"):
    text = ""
    for i in range(n):
        text += build_next(text, ts=f"2026-11-26T01:{i:02d}:00Z", agent="ben", kind=kind, task_id="T-12",
                           payload={"i": i})
    return text


def test_append_chain_and_verify():
    text = chain(5)
    rep = verify(text)
    assert rep.valid == 5 and rep.torn_offset is None and rep.broken_at is None and rep.ok
    first = json.loads(text.splitlines()[0])
    assert first["prev_sha256"] == GENESIS and len(first["event_id"]) == 16


def test_torn_tail_is_reported_with_byte_offset():
    text = chain(3) + '{"ts": "2026-11-26T02:00'
    rep = verify(text)
    assert rep.valid == 3 and rep.torn_offset == len(chain(3).encode("utf-8")) and not rep.ok
    with pytest.raises(DomainError, match="cola rota"):
        build_next(text, ts="2026-11-26T03:00:00Z", agent="ben", kind="note")
    full_invalid = chain(2) + "no-json\n"
    assert verify(full_invalid).torn_offset == len(chain(2).encode("utf-8"))


def test_middle_edit_breaks_chain():
    lines = chain(4).splitlines(keepends=True)
    ev = json.loads(lines[1])
    ev["payload"] = {"i": 99}
    lines[1] = json.dumps(ev, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    rep = verify("".join(lines))
    assert rep.broken_at == 3 and not rep.ok  # la línea 3 ya no encadena con la 2 editada


def test_event_validation():
    with pytest.raises(DomainError):
        build_next("", ts="x", agent="ben", kind="note")
    with pytest.raises(DomainError):
        build_next("", ts="2026-11-26T01:00:00Z", agent="ben", kind="inventado")
    with pytest.raises(DomainError):
        build_next("", ts="2026-11-26T01:00:00Z", agent="ben", kind="note", payload={"t": "x" * 600})
    with pytest.raises(DomainError, match="taint"):
        build_next("", ts="2026-11-26T01:00:00Z", agent="ben", kind="note", payload={"t": "<system>x</system>"})


def test_merkle_root():
    a, b, c = "a" * 64, "b" * 64, "c" * 64
    assert merkle_root([]) == merkle_root([])
    assert merkle_root([a]) == a
    assert merkle_root([a, b]) != merkle_root([b, a])
    assert merkle_root([a, b, c]) == merkle_root([a, b, c, c])
    assert verify(chain(3)).merkle_root == merkle_root(verify(chain(3)).hashes)


def _dec(text, ts, agent, did, statement, status="vigente", **kw):
    payload = {"id": did, "statement": statement, "status": status, **kw}
    return text + build_next(text, ts=ts, agent=agent, kind="decision", payload=payload)


def test_ledger_lww_and_contradictions():
    t = _dec("", "2026-11-26T01:00:00Z", "ben", "D-1", "Merge por fast-forward")
    t = _dec(t, "2026-11-26T01:05:00Z", "tween-forge", "D-1", "Merge por fast-forward con CI verde")
    t = _dec(t, "2026-11-26T01:06:00Z", "ben", "D-2", "Usar squash", status="revocada")
    t += build_next(t, ts="2026-11-26T01:07:00Z", agent="ben", kind="contradiction",
                    payload={"id": "C-1", "statement": "README dice 3 bots, memoria dice 2", "status": "abierta"})
    state = fold_ledger(t)
    assert state["decisions"]["D-1"]["statement"].endswith("CI verde")
    assert state["decisions"]["D-2"]["status"] == "revocada"
    assert state["contradictions"]["C-1"]["status"] == "abierta"
    md = render_ledger(state)
    assert "| D-1 |" in md and "C-1" in md and "Usar squash" in md


def test_ledger_merge_is_a_crdt():
    def st(entries):
        t = ""
        for ts, agent, did, s in entries:
            t = _dec(t, ts, agent, did, s)
        return fold_ledger(t)

    a = st([("2026-11-26T01:00:00Z", "ben", "D-1", "uno")])
    b = st([("2026-11-26T01:00:00Z", "tween-forge", "D-1", "uno-b"), ("2026-11-26T02:00:00Z", "ben", "D-2", "dos")])
    c = st([("2026-11-26T03:00:00Z", "federica-fondos", "D-1", "tres")])
    for x, y in itertools.permutations([a, b, c], 2):
        assert merge_ledgers(x, y) == merge_ledgers(y, x)
    assert merge_ledgers(merge_ledgers(a, b), c) == merge_ledgers(a, merge_ledgers(b, c))
    assert merge_ledgers(a, a) == a
    m = merge_ledgers(a, b)
    assert m["decisions"]["D-1"]["statement"] == "uno-b"  # mismo ts: desempata el agente
    assert merge_ledgers(m, c)["decisions"]["D-1"]["statement"] == "tres"


def test_ledger_rejects_bad_ids_and_status():
    with pytest.raises(DomainError):
        _dec("", "2026-11-26T01:00:00Z", "ben", "X-1", "s")
    with pytest.raises(DomainError):
        _dec("", "2026-11-26T01:00:00Z", "ben", "D-1", "s", status="quizás")


def test_same_second_same_agent_later_event_wins():
    t = ""
    for status in ("abierta", "resuelta", "abierta", "resuelta"):
        t += build_next(t, ts="2026-11-26T01:00:00Z", agent="ben", kind="contradiction",
                        payload={"id": "C-1", "statement": "x", "status": status})
    assert fold_ledger(t)["contradictions"]["C-1"]["status"] == "resuelta"
