"""I-DOM: el dominio no hace I/O ni lee el reloj del sistema."""
import ast
from pathlib import Path

DOMAIN = Path(__file__).resolve().parents[1] / "cortex9_memory" / "domain"
FORBIDDEN_MODULES = {"sqlite3", "os", "pathlib", "subprocess", "socket", "urllib", "fcntl", "shutil", "io", "time"}
FORBIDDEN_CALLS = {("datetime", "now"), ("datetime", "utcnow"), ("date", "today")}


def _modules():
    return sorted(p for p in DOMAIN.glob("*.py"))


def test_domain_has_no_forbidden_imports():
    offenders = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in FORBIDDEN_MODULES:
                    offenders.append(f"{path.name}: {n}")
    assert offenders == []


def test_domain_never_reads_wall_clock():
    offenders = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                base = node.func.value
                if isinstance(base, ast.Name) and (base.id, node.func.attr) in FORBIDDEN_CALLS:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_domain_does_not_import_outer_layers():
    for path in _modules():
        src = path.read_text(encoding="utf-8")
        assert "from ..application" not in src and "from ..adapters" not in src
