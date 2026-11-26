import pytest

from cortex9_memory.domain.context import CacheEntry, cache_key, check_entry, compact_todo
from cortex9_memory.domain.models import DomainError

TODO = """<todo_update>
- **COMPLETED**: Publicar framework (id: framework)
- **PENDING**: Decisiones de Federico sobre repos (id: publish-batch)
- **IN_PROGRESS**: Verificar requisitos (id: frontier)
- **COMPLETED**: README de perfil (id: profile-readme)
- **BLOCKED**: algo con estado raro (id: raro)
</todo_update>"""


def test_compact_keeps_active_and_loses_nothing():
    c = compact_todo(TODO)
    assert c.n_active == 3 and c.n_archived == 2 and c.n_items == 5
    assert "framework" not in c.active_text and "publish-batch" in c.active_text and "raro" in c.active_text
    assert "<todo_update>" in c.active_text
    assert "profile-readme" in c.archived_text
    assert c.tokens_after < c.tokens_before and c.tokens_saved == c.tokens_before - c.tokens_after


def test_compact_cancelled_and_idempotent():
    c = compact_todo("- **CANCELLED**: x (id: a)\n- **PENDING**: y (id: b)\n")
    assert c.n_archived == 1
    again = compact_todo(c.active_text)
    assert again.n_archived == 0 and again.active_text == c.active_text


def test_cache_key_normalizes_query():
    assert cache_key("transcript", "Tween  Forge handoff") == cache_key("transcript", "tween forge HANDOFF")
    assert cache_key("transcript", "x") != cache_key("report", "x")
    with pytest.raises(DomainError):
        cache_key("", "x")


def _e(**kw):
    base = {"key": cache_key("report", "q"), "kind": "report", "query": "q", "created_at": "2026-11-26T12:00:00Z",
            "ttl_s": 3600, "pointers": {"/a.txt": "aa"}}
    base.update(kw)
    return CacheEntry(**base)


def test_cache_validity_ttl_and_pointer_hashes():
    e = _e()
    assert check_entry(e, "2026-11-26T12:30:00Z", {"/a.txt": "aa"}) == (True, "hit")
    assert check_entry(e, "2026-11-26T13:00:01Z", {"/a.txt": "aa"})[1] == "ttl vencido"
    ok, why = check_entry(e, "2026-11-26T12:30:00Z", {"/a.txt": "bb"})
    assert not ok and "/a.txt" in why
    assert not check_entry(e, "2026-11-26T12:30:00Z", {"/a.txt": None})[0]
    assert CacheEntry.from_dict(e.to_dict()) == e
    with pytest.raises(DomainError):
        _e(ttl_s=0)
