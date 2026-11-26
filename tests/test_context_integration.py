import json

from cortex9_memory.adapters.cache_fs import FsCacheStore
from cortex9_memory.adapters.hasher_fs import FsFileHasher
from cortex9_memory.adapters.jsonl_log import JsonlFileLog
from cortex9_memory.application.context import CacheService
from cortex9_memory.cli import main


class Clock:
    t = "2026-11-26T12:00:00Z"

    def now(self):
        return self.t


def test_cache_hit_then_invalidated_by_input_change(tmp_path):
    src = tmp_path / "transcript.txt"
    src.write_text("original")
    clock = Clock()
    svc = CacheService(FsCacheStore(tmp_path / "c"), FsFileHasher(), JsonlFileLog(tmp_path / "c" / "s.jsonl"), clock)
    svc.put("transcript", "patrones caros TF", "resumen de 40 tokens", [str(src)], ttl_s=3600)
    assert svc.get("transcript", "Patrones  caros tf") == ("resumen de 40 tokens", "hit")
    src.write_text("cambió")
    content, why = svc.get("transcript", "patrones caros TF")
    assert content is None and "insumos cambiados" in why
    st = svc.stats()
    assert st["hits"] == 1 and st["misses"] == 1 and st["tokens_saved_est"] > 0


def test_cli_todo_compact_and_cache(tmp_path, capsys):
    todo = tmp_path / "todo.md"
    todo.write_text("- **COMPLETED**: a (id: a)\n- **PENDING**: b (id: b)\n")
    arch = tmp_path / "arch.md"
    assert main(["todo-compact", "--file", str(todo), "--archive", str(arch), "--out", str(tmp_path / "o.md")]) == 0
    summary = json.loads(capsys.readouterr().err)
    assert summary["archived"] == 1 and "(id: a)" in arch.read_text() and "(id: b)" in (tmp_path / "o.md").read_text()
    base = ["--cache-dir", str(tmp_path / "cache")]
    assert main(base + ["cache-get", "--kind", "k", "--query", "q"]) == 3
    content = tmp_path / "r.txt"
    content.write_text("res")
    assert main(base + ["cache-put", "--kind", "k", "--query", "q", "--file", str(content)]) == 0
    capsys.readouterr()
    assert main(base + ["cache-get", "--kind", "k", "--query", "q"]) == 0 and capsys.readouterr().out == "res"
    assert main(base + ["cache-put", "--kind", "k", "--query", "q", "--file", str(content),
                        "--pointer", str(tmp_path / "nope")]) == 1
