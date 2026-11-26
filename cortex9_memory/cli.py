"""CLI `c9mem`. DB por defecto: $C9MEM_DB o ./data/c9mem.sqlite"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .adapters.sqlite_repo import SqliteMemoryRepository
from .application.service import MemoryService, dumps
from .domain.models import ENTITY_TYPES, MEMORY_TYPES, DomainError


def default_db() -> str:
    return os.environ.get("C9MEM_DB") or str(Path.cwd() / "data" / "c9mem.sqlite")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="c9mem", description="Memoria compartida CORTEX-9 (SQLite)")
    p.add_argument("--db", default=default_db())
    p.add_argument("--agent", default=os.environ.get("C9MEM_AGENT"), help="nombre del agente que escribe")
    p.add_argument("--events-log", default=os.environ.get("C9MEM_EVENTS_LOG") or str(Path.cwd() / "events" / "c9-events.jsonl"))
    p.add_argument("--usage-log", default=os.environ.get("C9MEM_USAGE_LOG") or str(Path.cwd() / "usage" / "usage-log.jsonl"))
    p.add_argument("--shares-log", default=os.environ.get("C9MEM_SHARES_LOG") or str(Path.cwd() / "usage" / "shares.jsonl"))
    p.add_argument("--capsules-dir", default=os.environ.get("C9MEM_CAPSULES_DIR") or str(Path.cwd() / "capsules"))
    sub = p.add_subparsers(dest="cmd", required=True)

    ini = sub.add_parser("init", help="migrar + seeds estructurales")
    ini.add_argument("--workflows-dir", help="directorio opcional con */SKILL.md para sincronizar")
    sub.add_parser("migrate", help="aplicar migraciones pendientes")
    sub.add_parser("doctor", help="integridad, FKs, checksums, FTS, referencias colgantes")
    sub.add_parser("stats", help="conteo por tabla")

    f = sub.add_parser("fact-add")
    f.add_argument("type", choices=MEMORY_TYPES)
    f.add_argument("text")
    f.add_argument("--confidence", type=float, help="0..1; si se omite se deriva de --label o 0.8")
    f.add_argument("--label", choices=["alta", "media", "estimacion", "no_encontrado"])
    f.add_argument("--meta", default="{}", help="JSON")
    f.add_argument("--source", default="")
    f.add_argument("--source-key", help="clave idempotente para upsert multi-agente")
    f.add_argument("--valid-from")
    f.add_argument("--valid-until")
    f.add_argument("--session", type=int)
    f.add_argument("--trust", default="T2", help="procedencia: T0 externo, T1 bot, T2 verificado, T3 usuario")
    f.add_argument("--tainted", action="store_true", help="marcar como derivado de insumo no confiable")

    tc = sub.add_parser("taint-check", help="clasificar un texto con el taint-guard (F27)")
    tc.add_argument("text", nargs="?", help="texto; si se omite, se lee --file")
    tc.add_argument("--file")
    tc.add_argument("--trust", default="T1")

    s = sub.add_parser("fact-search")
    s.add_argument("query", nargs="?")
    s.add_argument("--type", choices=MEMORY_TYPES)
    s.add_argument("--all", action="store_true", help="incluir vencidos/supersedidos")
    s.add_argument("--as-of", help="consulta puntual ISO-8601 (qué era cierto en esa fecha)")
    s.add_argument("--touch", action="store_true", help="incrementar access_count de los resultados")
    s.add_argument("--half-life", type=float, default=30.0, help="vida media en días para decay (default 30)")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")

    ss = sub.add_parser("fact-supersede")
    ss.add_argument("old_id", type=int)
    ss.add_argument("new_id", type=int)

    se = sub.add_parser("session-start")
    se.add_argument("--channel", default="chat")
    se.add_argument("--title", default="")
    se.add_argument("--summary", default="")

    sm = sub.add_parser("session-summarize-add")
    sm.add_argument("session_id", type=int)
    sm.add_argument("turn_range", help="ej. t269u-t281u")
    sm.add_argument("summary")
    sm.add_argument("--tokens", type=int)
    sm.add_argument("--source-file", help="tramo original: si el resumen pierde entidades críticas, se rechaza (F35)")

    sub.add_parser("archetype-list")

    ia = sub.add_parser("instrument-add")
    ia.add_argument("acronym")
    ia.add_argument("name")
    ia.add_argument("definition")
    ia.add_argument("--refutation", default="NO ENCONTRADO")
    ia.add_argument("--kind", help="T/R/I/F (por defecto, la 1ª letra del acrónimo)")
    ia.add_argument("--workstream", default="")
    ia.add_argument("--source", default="")

    ih = sub.add_parser("instrument-harvest", help="extraer (XXXX-FNBR) de un .md")
    ih.add_argument("path")
    ih.add_argument("--workstream", required=True)

    il = sub.add_parser("instrument-list")
    il.add_argument("--workstream")

    sk = sub.add_parser("skill-register")
    sk.add_argument("slug")
    sk.add_argument("title")
    sk.add_argument("path_or_body")
    sk.add_argument("--version")
    sk.add_argument("--portable-path", default="")

    sy = sub.add_parser("skill-sync", help="ingesta en solo lectura de */SKILL.md")
    sy.add_argument("--dir", default=os.environ.get("C9MEM_WORKFLOWS_DIR", ""))
    sy.add_argument("--link-archetypes", action="store_true")

    lk = sub.add_parser("link", help="vincular skill con entidad")
    lk.add_argument("skill")
    lk.add_argument("entity_type", choices=ENTITY_TYPES)
    lk.add_argument("entity", help="id o clave natural (slug/acrónimo/nombre)")
    lk.add_argument("--note", default="")

    rl = sub.add_parser("relate")
    rl.add_argument("from_type", choices=ENTITY_TYPES)
    rl.add_argument("from_key")
    rl.add_argument("relation")
    rl.add_argument("to_type", choices=ENTITY_TYPES)
    rl.add_argument("to_key")
    rl.add_argument("--note", default="")
    rl.add_argument("--valid-from")
    rl.add_argument("--valid-until")

    ri = sub.add_parser("relation-invalidate", help="cerrar vigencia temporal de una relación")
    ri.add_argument("relation_id", type=int)
    ri.add_argument("--until")

    nb = sub.add_parser("neighbors")
    nb.add_argument("entity_type", choices=ENTITY_TYPES)
    nb.add_argument("entity")
    nb.add_argument("--as-of", help="consulta puntual ISO-8601")
    nb.add_argument("--all", action="store_true", help="incluir relaciones vencidas")

    cp = sub.add_parser("context-pack", help="armar bloque compacto dentro de presupuesto de tokens")
    cp.add_argument("--agent", default=os.environ.get("C9MEM_AGENT", "primary"))
    cp.add_argument("--budget", type=int, default=800)
    cp.add_argument("--query")
    cp.add_argument(
        "--touch",
        action="store_true",
        help="incrementar access_count de los hechos incluidos (desactivado por defecto)",
    )
    cp.add_argument("--json", action="store_true")
    cp.add_argument("--strategy", choices=["knapsack", "greedy"], default="knapsack",
                    help="knapsack: óptimo con dedup y MMR (F15/F17); greedy: llenado secuencial v0.2")

    bk = sub.add_parser("backup", help="snapshot consistente con rotación")
    bk.add_argument("--dir", default=os.environ.get("C9MEM_BACKUP_DIR", "./backups"))
    bk.add_argument("--keep", type=int, default=14)

    ex = sub.add_parser("export-json")
    ex.add_argument("--out", help="archivo; por defecto stdout")
    ex.add_argument("--tables", help="lista separada por comas para un export parcial (p. ej. para Grok)")

    im = sub.add_parser("import-json")
    im.add_argument("path")
    im.add_argument("--mode", choices=["merge", "replace"], default="merge")

    cn = sub.add_parser("capsule-new", help="crear cápsula de tarea <= 1 página (F08)")
    cn.add_argument("task_id")
    cn.add_argument("--owner", required=True)
    cn.add_argument("--estado", required=True)
    cn.add_argument("--status", default="en_progreso")
    cn.add_argument("--decision", action="append", default=[])
    cn.add_argument("--next", action="append", default=[], dest="proximos")
    cn.add_argument("--pointer", action="append", default=[], help="ruta absoluta[#Lx-Ly][@sha]")
    cn.add_argument("--collaborator", action="append", default=[])
    cn.add_argument("--ttl", type=int, default=48)
    cn.add_argument("--budget", type=int, default=450)

    cs = sub.add_parser("capsule-show", help="leer cápsula + staleness (TTL y sha256 de punteros)")
    cs.add_argument("task_id")
    cs.add_argument("--json", action="store_true")

    cpat = sub.add_parser("capsule-patch", help="parche con CAS sobre rev")
    cpat.add_argument("task_id")
    cpat.add_argument("--rev", type=int, required=True, help="revisión leída (I-CAS)")
    cpat.add_argument("--set", action="append", default=[], metavar="CAMPO=VALOR")
    cpat.add_argument("--add", action="append", default=[], metavar="CAMPO=VALOR")
    cpat.add_argument("--remove", action="append", default=[], metavar="CAMPO=INDICE")
    cpat.add_argument("--ops", help="lista JSON de operaciones")

    sub.add_parser("capsule-list")

    ur = sub.add_parser("usage-record", help="registrar lectura del uso semanal (F31)")
    ur.add_argument("--pct", type=float, required=True)
    ur.add_argument("--reset-at", required=True, help="ISO-8601 con zona, p. ej. 2026-11-26T00:37:00-03:00")
    ur.add_argument("--task")
    ur.add_argument("--phase", choices=["before", "after", "point"], default="point")
    ur.add_argument("--tokens", type=int)
    ur.add_argument("--source", default="platform")
    sub.add_parser("usage-status", help="nivel 50/75/90 y política vigente")
    ui = sub.add_parser("usage-ingest", help="guardar un reporte de uso por bot/actividad/día/routine (F39)")
    ui.add_argument("--file", help="texto del reporte; si se omite, stdin")
    ui.add_argument("--reset-at", help="ISO-8601; si se pasa, la lectura N%% también alimenta el semáforo")
    sub.add_parser("usage-burn", help="velocidad de quema y proyección de agotamiento (F39)")
    ua = sub.add_parser("usage-admit", help="¿se puede lanzar esta tarea sin cruzar el techo?")
    ua.add_argument("--kind", choices=["pesada", "liviana", "cierre"], required=True)
    ua.add_argument("--delta", type=float, help="puntos porcentuales estimados")
    ua.add_argument("--tokens", type=int, help="tokens estimados (usa calibración)")

    ea = sub.add_parser("event-append", help="agregar evento encadenado al log (F25)")
    ea.add_argument("kind")
    ea.add_argument("--task")
    ea.add_argument("--payload", default="{}", help="objeto JSON chico (<= 120 tokens)")
    ea.add_argument("--ref", help="puntero a contenido en disco")
    ev = sub.add_parser("event-verify", help="verificar cadena y raíz Merkle")
    ev.add_argument("--repair", action="store_true", help="recortar una cola rota (nunca una ruptura en el medio)")
    dc = sub.add_parser("decide", help="registrar decisión D-n en el ledger (F21)")
    dc.add_argument("id")
    dc.add_argument("statement")
    dc.add_argument("--status", default="vigente", choices=["vigente", "supersedida", "revocada"])
    dc.add_argument("--supersedes")
    dc.add_argument("--task")
    ct = sub.add_parser("contradict", help="registrar contradicción C-n")
    ct.add_argument("id")
    ct.add_argument("statement")
    ct.add_argument("--status", default="abierta", choices=["abierta", "resuelta"])
    ct.add_argument("--resolved-by")
    ct.add_argument("--task")
    lg = sub.add_parser("ledger", help="vista DECISIONS.md generada del log")
    lg.add_argument("--out")

    ca = sub.add_parser("codebook-add", help="alias compartido @{NOMBRE} -> expansión (F02)")
    ca.add_argument("name")
    ca.add_argument("expansion")
    sub.add_parser("codebook-list")
    for name in ("encode", "decode"):
        x = sub.add_parser(name, help=f"{name} con el codebook (sin pérdida)")
        x.add_argument("text", nargs="?")
        x.add_argument("--file")
    fo = sub.add_parser("fold", help="plegar salida larga de herramienta (F07)")
    fo.add_argument("--file", required=True)
    fo.add_argument("--ref", help="ruta a citar para el texto completo (por defecto --file)")
    fo.add_argument("--max-lines", type=int, default=40)
    pr = sub.add_parser("summary-probe", help="¿el resumen conserva las entidades críticas? (F35)")
    pr.add_argument("--source", required=True, help="archivo original")
    pr.add_argument("--summary", required=True, help="archivo del resumen")
    for name in ("handoff-prepare", "handoff-check", "handoff-result"):
        h = sub.add_parser(name, help="sobre de handoff por punteros (F06)")
        h.add_argument("--file", required=True, help="JSON del sobre")
    return p


def _kv(items: list[str], op: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for it in items:
        k, sep, v = it.partition("=")
        if not sep:
            raise DomainError(f"usar CAMPO=VALOR: {it!r}")
        out.append({"op": op, "field": k, "index": int(v)} if op == "remove" else {"op": op, "field": k, "value": v})
    return out


def _tools(a: argparse.Namespace) -> int:
    from .adapters.hasher_fs import FsFileHasher
    from .adapters.jsonl_log import JsonlFileLog
    from .adapters.sqlite_repo import UtcClock
    from .application.budget import BudgetService
    from .application.events import EventService
    from .application.handoff import HandoffService
    from .domain.compression import fold_output

    if a.cmd == "fold":
        folded, stats = fold_output(Path(a.file).read_text(encoding="utf-8", errors="replace"),
                                    ref=a.ref or str(Path(a.file).resolve()), max_lines=a.max_lines)
        print(folded)
        print(f"<!-- fold: {json.dumps(stats)} -->")
        return 0
    clock = UtcClock()
    svc = HandoffService(FsFileHasher(), BudgetService(JsonlFileLog(a.usage_log), clock),
                         EventService(JsonlFileLog(a.events_log), clock))
    raw = json.loads(Path(a.file).read_text(encoding="utf-8"))
    if a.cmd == "handoff-prepare":
        print(dumps(svc.prepare(raw)))
    elif a.cmd == "handoff-check":
        res = svc.check(raw.get("envelope", raw))
        print(dumps(res))
        return 0 if res["ok"] else 4
    else:
        print(dumps(svc.result(raw)))
    return 0


def _events(a: argparse.Namespace) -> int:
    from .adapters.jsonl_log import JsonlFileLog
    from .adapters.sqlite_repo import UtcClock
    from .application.events import EventService

    svc = EventService(JsonlFileLog(a.events_log), UtcClock())
    agent = a.agent or "primary"
    if a.cmd == "event-append":
        print(dumps(svc.append(agent, a.kind, a.task, json.loads(a.payload), a.ref)))
    elif a.cmd == "event-verify":
        rep = svc.verify(a.repair)
        print(dumps(rep))
        return 0 if rep["ok"] else 7
    elif a.cmd == "decide":
        print(dumps(svc.decide(agent, a.id, a.statement, a.status, a.supersedes, a.task)))
    elif a.cmd == "contradict":
        print(dumps(svc.contradict(agent, a.id, a.statement, a.status, a.resolved_by, a.task)))
    else:
        text = svc.ledger()
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(text, encoding="utf-8")
            print(a.out)
        else:
            print(text, end="")
    return 0


def _usage(a: argparse.Namespace) -> int:
    from .adapters.jsonl_log import JsonlFileLog
    from .adapters.sqlite_repo import UtcClock
    from .application.budget import BudgetService
    from .domain.scoring import parse_iso

    svc = BudgetService(JsonlFileLog(a.usage_log), UtcClock())
    if a.cmd in ("usage-ingest", "usage-burn"):
        from .application.usage_ledger import LedgerService

        led = LedgerService(JsonlFileLog(a.shares_log), svc, UtcClock())
        if a.cmd == "usage-burn":
            print(dumps(led.burn()))
            return 0
        text = Path(a.file).read_text(encoding="utf-8") if a.file else sys.stdin.read()
        reset = parse_iso(a.reset_at).isoformat().replace("+00:00", "Z") if a.reset_at else None
        print(dumps(led.ingest(text, a.agent or "primary", reset)))
        return 0
    if a.cmd == "usage-record":
        reset = parse_iso(a.reset_at).isoformat().replace("+00:00", "Z")
        st = svc.record(a.pct, reset, a.agent or "primary", task_id=a.task, phase=a.phase, tokens_est=a.tokens,
                        source=a.source)
    elif a.cmd == "usage-status":
        st = svc.status()
    else:
        res = svc.admit(a.kind, a.delta, a.tokens)
        print(dumps(res))
        return 0 if res["allowed"] else 6
    print(dumps({"level": st.level, "pct": st.pct, "reset_at": st.reset_at, "policy": st.policy, "alert": st.alert,
                 "readings": st.readings}))
    return 0


def _capsules(a: argparse.Namespace) -> int:
    from .adapters.capsule_fs import FsCapsuleStore
    from .adapters.hasher_fs import FsFileHasher
    from .adapters.sqlite_repo import UtcClock
    from .application.capsules import CapsuleService

    svc = CapsuleService(FsCapsuleStore(a.capsules_dir), FsFileHasher(), UtcClock())
    agent = a.agent or "primary"
    if a.cmd == "capsule-new":
        cap = svc.new(a.task_id, a.owner, a.estado, agent, status=a.status, decisiones=a.decision,
                      proximos=a.proximos, punteros=a.pointer, collaborators=a.collaborator,
                      ttl_hours=a.ttl, budget_tokens=a.budget)
        print(dumps(cap.meta()))
    elif a.cmd == "capsule-show":
        info = svc.show(a.task_id)
        print(dumps(info) if a.json else info["text"] + f"<!-- staleness: {json.dumps(info['staleness'])} -->")
        return 0 if info["staleness"]["ok"] else 4
    elif a.cmd == "capsule-patch":
        ops = _kv(a.set, "set") + _kv(a.add, "add") + _kv(a.remove, "remove")
        if a.ops:
            ops += json.loads(a.ops)
        print(dumps(svc.patch(a.task_id, ops, a.rev, agent).meta()))
    else:
        print(dumps(svc.list()))
    return 0


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    if a.cmd == "fold" or a.cmd.startswith("handoff-"):
        try:
            return _tools(a)
        except DomainError as e:
            print(f"error: {e}", file=sys.stderr)
            return 9 if str(e).startswith("aborted_budget") else 1
    if a.cmd in ("event-append", "event-verify", "decide", "contradict", "ledger"):
        try:
            return _events(a)
        except DomainError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    if a.cmd.startswith("usage-"):
        try:
            return _usage(a)
        except DomainError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    if a.cmd.startswith("capsule-"):
        try:
            return _capsules(a)
        except DomainError as e:
            print(f"error: {e}", file=sys.stderr)
            return 5 if type(e).__name__ == "ConflictError" else 1
    repo = SqliteMemoryRepository(a.db)
    svc = MemoryService(repo)
    try:
        if a.cmd not in ("init", "migrate"):
            repo.migrate()
        c = a.cmd
        if c == "init":
            print(dumps(svc.init(workflows_dir=a.workflows_dir)))
        elif c == "migrate":
            print(dumps({"applied": repo.migrate()}))
        elif c == "doctor":
            problems = repo.doctor()
            print(dumps({"ok": not problems, "problems": problems, "tables": repo.table_names(), "db": a.db}))
            return 0 if not problems else 2
        elif c == "stats":
            print(dumps(repo.stats()))
        elif c == "fact-add":
            fid = svc.add_fact(
                type=a.type,
                fact_text=a.text,
                confidence=a.confidence,
                evidence_label=a.label,
                meta_json=a.meta,
                source=a.source,
                source_key=a.source_key,
                valid_from=a.valid_from,
                valid_until=a.valid_until,
                agent=a.agent,
                session_id=a.session,
                trust_tier=a.trust,
                tainted=a.tainted,
            )
            print(fid)
        elif c == "taint-check":
            body = Path(a.file).read_text(encoding="utf-8") if a.file else (a.text or "")
            v = svc.taint_check(body, a.trust)
            print(dumps({"status": v.status, "reasons": list(v.reasons), "tier": v.tier.name}))
            return 0 if not v.blocked else 3
        elif c == "fact-search":
            rows = svc.search(
                a.query,
                a.type,
                a.all,
                a.limit,
                as_of=a.as_of,
                touch=a.touch,
                half_life_days=a.half_life,
            )
            if a.json:
                print(dumps(rows))
            else:
                for r in rows:
                    print(
                        f"[{r['id']}] {r['type']} ({r['evidence_label']} {r['confidence']:.2f} s={r['score']:.2f}) "
                        f"{r['fact_text']}" + (f"  — {r['source']}" if r["source"] else "")
                    )
        elif c == "fact-supersede":
            svc.supersede(a.old_id, a.new_id)
            print("ok")
        elif c == "session-start":
            print(svc.start_session(a.agent, a.channel, a.title, a.summary))
        elif c == "session-summarize-add":
            src = Path(a.source_file).read_text(encoding="utf-8") if a.source_file else None
            print(svc.add_summary(a.session_id, a.turn_range, a.summary, a.tokens, a.agent, source_text=src))
        elif c == "archetype-list":
            for r in repo.list_archetypes():
                print(f"{r['slug']}: {r['name']} — {r['definition']}")
        elif c == "instrument-add":
            print(
                svc.add_instrument(
                    acronym=a.acronym,
                    name=a.name,
                    kind=a.kind or a.acronym[:1],
                    definition=a.definition,
                    refutation=a.refutation,
                    related_workstream=a.workstream,
                    source=a.source,
                    agent=a.agent,
                )
            )
        elif c == "instrument-harvest":
            print(dumps(svc.harvest_instruments(a.path, a.workstream, a.agent)))
        elif c == "instrument-list":
            for r in repo.list_instruments(a.workstream):
                print(f"{r['acronym']} [{r['related_workstream']}] {r['name']}")
        elif c == "skill-register":
            print(svc.register_skill(a.slug, a.title, a.path_or_body, a.version, a.portable_path))
        elif c == "skill-sync":
            if not a.dir:
                raise DomainError("especificá --dir o C9MEM_WORKFLOWS_DIR")
            print(dumps(svc.sync_skills(a.dir, link_archetypes=a.link_archetypes)))
        elif c == "link":
            print(svc.link(a.skill, a.entity_type, a.entity, a.note))
        elif c == "relate":
            print(
                svc.relate(
                    a.from_type,
                    a.from_key,
                    a.relation,
                    a.to_type,
                    a.to_key,
                    a.note,
                    a.agent,
                    a.valid_from,
                    a.valid_until,
                )
            )
        elif c == "relation-invalidate":
            svc.invalidate_relation(a.relation_id, a.until)
            print("ok")
        elif c == "neighbors":
            eid = repo.find_id(a.entity_type, a.entity)
            if eid is None:
                raise DomainError(f"{a.entity_type} {a.entity} no existe")
            print(dumps(repo.neighbors(a.entity_type, eid, as_of=a.as_of, include_expired=a.all)))
        elif c == "context-pack":
            pack = svc.context_pack(a.agent, a.budget, a.query, touch=a.touch, strategy=a.strategy)
            print(dumps(pack) if a.json else pack["text"])
        elif c == "backup":
            result = svc.backup(a.dir, repo.clock.now(), a.keep)
            print(dumps(result))
            return 0 if result.get("integrity") == "ok" else 2
        elif c == "export-json":
            data = svc.export(repo.clock.now())
            if a.tables:
                keep = {t.strip() for t in a.tables.split(",")}
                data["tables"] = {k: v for k, v in data["tables"].items() if k in keep}
            text = dumps(data)
            if a.out:
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.out).write_text(text, encoding="utf-8")
                print(a.out)
            else:
                print(text)
        elif c == "codebook-add":
            svc.codebook_add(a.name, a.expansion, a.agent)
            print("ok")
        elif c == "codebook-list":
            for a_ in svc.aliases():
                print(f"@{{{a_.name}}} = {a_.expansion}")
        elif c in ("encode", "decode"):
            body = Path(a.file).read_text(encoding="utf-8") if a.file else (a.text or "")
            print(svc.encode(body) if c == "encode" else svc.decode(body), end="" if a.file else "\n")
        elif c == "summary-probe":
            res = svc.probe(Path(a.source).read_text(encoding="utf-8"), Path(a.summary).read_text(encoding="utf-8"))
            print(dumps(res))
            return 0 if res["ok"] else 8
        elif c == "import-json":
            print(dumps(svc.import_(json.loads(Path(a.path).read_text(encoding="utf-8")), a.mode)))
        return 0
    except DomainError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        repo.close()
