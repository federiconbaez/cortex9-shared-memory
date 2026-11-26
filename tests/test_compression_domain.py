import random

import pytest

from cortex9_memory.domain.compression import (
    Alias,
    critical_entities,
    decode,
    encode,
    fold_output,
    probe,
)
from cortex9_memory.domain.models import DomainError

BOOK = [Alias("R1", "/home/box/cortex9-memory"), Alias("ID", "Federico Nicolas Baez Rizzo"),
        Alias("R1t", "/home/box/cortex9-memory/tests")]


def test_encode_decode_shrinks_and_roundtrips():
    text = "Repo /home/box/cortex9-memory/tests de Federico Nicolas Baez Rizzo en /home/box/cortex9-memory"
    enc = encode(text, BOOK)
    assert "@{R1t}" in enc and "@{ID}" in enc and len(enc) < len(text)
    assert decode(enc, BOOK) == text


@pytest.mark.parametrize("seed", range(200))
def test_roundtrip_is_lossless_for_adversarial_text(seed):
    rng = random.Random(seed)
    alphabet = ["@", "{", "}", "@{", "R1", "ID", "@{R1}", "@@", "x", " ", "/home/box/cortex9-memory", "Federico"]
    text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
    assert decode(encode(text, BOOK), BOOK) == text


def test_alias_validation_and_unknown_alias():
    for bad in (("bad name", "expansion-larga"), ("ok", "corta"), ("ok", "con @ adentro aqui")):
        with pytest.raises(DomainError):
            Alias(*bad)
    with pytest.raises(DomainError):
        decode("@{NOPE}", BOOK)
    with pytest.raises(DomainError):
        encode("x", [Alias("A", "expansion-uno"), Alias("A", "expansion-dos")])


def test_fold_keeps_summary_and_unique_failures():
    lines = [f"tests/test_x.py::test_{i} PASSED" for i in range(300)]
    lines[50] = "FAILED tests/test_x.py::test_50 - AssertionError: 1 != 2"
    lines[51] = "FAILED tests/test_x.py::test_50 - AssertionError: 1 != 2"
    lines[120] = "E   KeyError: 'id'"
    lines.append("===== 1 failed, 299 passed in 0.9s =====")
    folded, stats = fold_output("\n".join(lines), ref="/tmp/pytest.log")
    assert "1 failed, 299 passed" in folded and "test_50" in folded and "KeyError" in folded
    assert folded.count("FAILED tests/test_x.py::test_50") == 1
    assert "/tmp/pytest.log" in folded and stats["omitted"] > 250
    assert stats["tokens_after"] < stats["tokens_before"] / 5


def test_fold_short_output_is_untouched():
    out, stats = fold_output("ok\nall good")
    assert out == "ok\nall good" and stats["omitted"] == 0


def test_critical_entities():
    src = ("Commit 1850e0e en /home/box/x.py, decisión D-3 y C-12, ref CX9-6207, "
           "uso 79.6% en t330u, ver https://github.com/a/b/pull/5")
    ents = critical_entities(src)
    assert {"1850e0e", "/home/box/x.py", "D-3", "C-12", "CX9-6207", "79.6%", "t330u",
            "https://github.com/a/b/pull/5"} <= ents


def test_probe_rejects_lossy_summary_and_accepts_faithful_one():
    src = "PR #5 abierto (rama feat/x), HEAD b8a57ce, decisión D-1, uso 81.2%, log en /workspace/bot-memory/e.jsonl"
    good = "D-1 vigente; HEAD b8a57ce; 81.2%; log /workspace/bot-memory/e.jsonl"
    bad = "Se abrió un PR y se tomó una decisión."
    assert probe(src, good)["ok"] is True
    res = probe(src, bad)
    assert res["ok"] is False and "b8a57ce" in res["missing"] and "D-1" in res["missing"]
    assert probe(src, encode(good, [Alias("BM", "/workspace/bot-memory")]),
                 [Alias("BM", "/workspace/bot-memory")])["ok"] is True
    assert probe("1850e0e7c9", "1850e0e")["ok"] is True  # prefijo de hash >= 7
