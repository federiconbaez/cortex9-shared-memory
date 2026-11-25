"""Adaptador SQLite (WAL + FTS5 + migraciones con checksum + scoring temporal)."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any

from ..domain.models import (
    Agent,
    Archetype,
    DomainError,
    Instrument,
    MemoryFact,
    MessageSummary,
    Relation,
    Session,
    Skill,
)
from ..domain.scoring import hybrid_score

TABLES_IN_ORDER = (
    "agents",
    "memory_types",
    "confidence_labels",
    "sessions",
    "message_summaries",
    "memory_facts",
    "archetypes",
    "instruments",
    "skills",
    "skill_links",
    "entity_relations",
    "confidence_label_usage",
)
_ENTITY_TABLE = {
    "agent": ("agents", "name"),
    "session": ("sessions", None),
    "summary": ("message_summaries", None),
    "fact": ("memory_facts", None),
    "archetype": ("archetypes", "slug"),
    "instrument": ("instruments", "acronym"),
    "skill": ("skills", "slug"),
}


class UtcClock:
    def now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _migrations() -> list[tuple[str, str]]:
    pkg = resources.files("cortex9_memory.migrations")
    files = sorted((p for p in pkg.iterdir() if p.name.endswith(".sql")), key=lambda p: p.name)
    return [(p.name, p.read_text(encoding="utf-8")) for p in files]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fts_query(q: str) -> str:
    toks = re.findall(r"\w+", q, flags=re.UNICODE)
    return " ".join(f'"{t}"*' for t in toks)


class SqliteMemoryRepository:
    def __init__(self, path: str | Path, clock: UtcClock | None = None) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or UtcClock()
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 30000")
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.db.close()

    # ---------- esquema ----------
    def migrate(self) -> list[str]:
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations(
            version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"""
        )
        done = {r["version"] for r in self.db.execute("SELECT version FROM schema_migrations")}
        applied: list[str] = []
        for name, sql in _migrations():
            if name in done:
                continue
            safe_name = name.replace("'", "''")
            record = f"INSERT INTO schema_migrations VALUES ('{safe_name}','{_sha(sql)}','{self.clock.now()}');"
            try:
                self.db.executescript("BEGIN;\n" + sql + "\n" + record + "\nCOMMIT;")
            except sqlite3.Error:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
            applied.append(name)
        return applied

    def doctor(self) -> list[str]:
        problems: list[str] = []
        if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            problems.append("integrity_check falló")
        fk = self.db.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            problems.append(f"foreign_key_check: {len(fk)} filas huérfanas")
        rows = {r["version"]: r["checksum"] for r in self.db.execute("SELECT * FROM schema_migrations")}
        for name, sql in _migrations():
            if name not in rows:
                problems.append(f"migración pendiente: {name}")
            elif rows[name] != _sha(sql):
                problems.append(f"checksum distinto en migración aplicada: {name}")
        for t in ("agent", "session", "summary", "fact", "archetype", "instrument", "skill"):
            table, _ = _ENTITY_TABLE[t]
            for col_t, col_i in (("from_type", "from_id"), ("to_type", "to_id")):
                n = self.db.execute(
                    f"SELECT count(*) FROM entity_relations WHERE {col_t}=? AND {col_i} NOT IN (SELECT id FROM {table})",
                    (t,),
                ).fetchone()[0]
                if n:
                    problems.append(f"entity_relations: {n} referencias colgantes a {t}")
            n = self.db.execute(
                f"SELECT count(*) FROM skill_links WHERE related_entity_type=? "
                f"AND related_entity_id NOT IN (SELECT id FROM {table})",
                (t,),
            ).fetchone()[0]
            if n:
                problems.append(f"skill_links: {n} referencias colgantes a {t}")
        n = self.db.execute("SELECT count(*) FROM memory_facts").fetchone()[0]
        m = self.db.execute("SELECT count(*) FROM memory_facts_fts").fetchone()[0]
        if n != m:
            problems.append(
                f"FTS desincronizado ({n} vs {m}); correr: INSERT INTO memory_facts_fts(memory_facts_fts) VALUES('rebuild')"
            )
        return problems

    def stats(self) -> dict[str, int]:
        out = {t: self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES_IN_ORDER}
        out["schema_migrations"] = self.db.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
        return out

    def table_names(self) -> list[str]:
        return [
            r[0]
            for r in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE 'memory_facts_fts_%' ORDER BY name"
            )
        ]

    # ---------- agentes / sesiones ----------
    def ensure_agent(self, agent: Agent) -> int:
        with self.db:
            self.db.execute(
                "INSERT INTO agents(name, role, created_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET role=CASE WHEN excluded.role<>'' THEN excluded.role ELSE role END",
                (agent.name, agent.role, self.clock.now()),
            )
        return self.db.execute("SELECT id FROM agents WHERE name=?", (agent.name,)).fetchone()[0]

    def agent_id(self, name: str | None) -> int | None:
        return self.ensure_agent(Agent(name)) if name else None

    def add_session(self, s: Session) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO sessions(agent_id, channel, title, started_at, summary) VALUES (?,?,?,?,?)",
                (self.agent_id(s.agent), s.channel, s.title, s.started_at or self.clock.now(), s.summary),
            )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def add_summary(self, m: MessageSummary) -> int:
        if not self.exists("session", m.session_id):
            raise DomainError(f"session {m.session_id} no existe")
        with self.db:
            cur = self.db.execute(
                "INSERT INTO message_summaries(session_id, turn_range, summary, tokens_est, agent_id, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (m.session_id, m.turn_range, m.summary, m.tokens_est, self.agent_id(m.agent), self.clock.now()),
            )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def recent_summaries(self, agent: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        if agent:
            rows = self.db.execute(
                "SELECT ms.*, a.name AS agent FROM message_summaries ms "
                "LEFT JOIN agents a ON a.id=ms.agent_id "
                "WHERE a.name=? ORDER BY ms.created_at DESC, ms.id DESC LIMIT ?",
                (agent, limit),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
        rows = self.db.execute(
            "SELECT ms.*, a.name AS agent FROM message_summaries ms "
            "LEFT JOIN agents a ON a.id=ms.agent_id ORDER BY ms.created_at DESC, ms.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- hechos ----------
    def add_fact(self, f: MemoryFact) -> int:
        now = self.clock.now()
        aid = self.agent_id(f.agent)
        label = f.resolved_label()
        conf = float(f.confidence if f.confidence is not None else 0.8)
        with self.db:
            if f.source_key:
                self.db.execute(
                    "INSERT INTO memory_facts(type, fact_text, meta_json, confidence, evidence_label, valid_from, "
                    "valid_until, source, agent_id, session_id, source_key, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(source_key) WHERE source_key IS NOT NULL DO UPDATE SET "
                    "type=excluded.type, fact_text=excluded.fact_text, meta_json=excluded.meta_json, "
                    "confidence=excluded.confidence, evidence_label=excluded.evidence_label, "
                    "valid_until=excluded.valid_until, source=excluded.source, agent_id=excluded.agent_id",
                    (
                        f.type,
                        f.fact_text,
                        f.meta_json,
                        conf,
                        label,
                        f.valid_from or now,
                        f.valid_until,
                        f.source,
                        aid,
                        f.session_id,
                        f.source_key,
                        now,
                    ),
                )
                return self.db.execute(
                    "SELECT id FROM memory_facts WHERE source_key=?", (f.source_key,)
                ).fetchone()[0]
            cur = self.db.execute(
                "INSERT INTO memory_facts(type, fact_text, meta_json, confidence, evidence_label, valid_from, "
                "valid_until, source, agent_id, session_id, source_key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f.type,
                    f.fact_text,
                    f.meta_json,
                    conf,
                    label,
                    f.valid_from or now,
                    f.valid_until,
                    f.source,
                    aid,
                    f.session_id,
                    None,
                    now,
                ),
            )
            assert cur.lastrowid is not None
            return cur.lastrowid

    def search_facts(
        self,
        query: str | None,
        type_: str | None,
        include_expired: bool,
        limit: int,
        as_of: str | None = None,
        touch: bool = False,
        half_life_days: float = 30.0,
    ) -> list[dict[str, Any]]:
        now = self.clock.now()
        ref_time = as_of or now
        where: list[str] = []
        args: list[Any] = []
        fts = _fts_query(query) if query else ""
        bm25_expr = "bm25(memory_facts_fts) AS _bm25" if fts else "NULL AS _bm25"
        sql = f"SELECT f.*, a.name AS agent, {bm25_expr} FROM memory_facts f LEFT JOIN agents a ON a.id=f.agent_id"
        if fts:
            sql += " JOIN memory_facts_fts ON memory_facts_fts.rowid=f.id"
            where.append("memory_facts_fts MATCH ?")
            args.append(fts)
        if type_:
            where.append("f.type=?")
            args.append(type_)
        if as_of:
            where.append("f.valid_from <= ? AND (f.valid_until IS NULL OR f.valid_until > ?)")
            args.extend([as_of, as_of])
        elif not include_expired:
            where.append("f.superseded_by IS NULL AND (f.valid_until IS NULL OR f.valid_until >= ?)")
            args.append(now)
        if where:
            sql += " WHERE " + " AND ".join(where)
        # Traemos un sobre-conjunto razonable para rankear con decay + uso en Python.
        pool = max(limit * 4, 50)
        sql += " ORDER BY _bm25, f.created_at DESC, f.id DESC LIMIT ?" if fts else " ORDER BY f.created_at DESC, f.id DESC LIMIT ?"
        args.append(pool)
        rows = [dict(r) for r in self.db.execute(sql, args)]
        for r in rows:
            ref_ts = r.get("last_accessed_at") or r["created_at"]
            r["score"] = hybrid_score(
                r.pop("_bm25", None),
                float(r["confidence"]),
                ref_ts,
                ref_time,
                int(r.get("access_count", 0)),
                half_life_days,
            )
        rows.sort(key=lambda r: (r["score"], r["id"]), reverse=True)
        selected = rows[:limit]
        if touch and selected:
            self.touch_facts([int(r["id"]) for r in selected])
        return selected

    def touch_facts(self, fact_ids: list[int]) -> None:
        if not fact_ids:
            return
        now = self.clock.now()
        placeholders = ",".join("?" * len(fact_ids))
        with self.db:
            self.db.execute(
                f"UPDATE memory_facts SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN ({placeholders})",
                [now, *fact_ids],
            )

    def supersede_fact(self, old_id: int, new_id: int) -> None:
        if not (self.exists("fact", old_id) and self.exists("fact", new_id)):
            raise DomainError("ambos hechos deben existir")
        with self.db:
            self.db.execute(
                "UPDATE memory_facts SET superseded_by=?, valid_until=COALESCE(valid_until, ?) WHERE id=?",
                (new_id, self.clock.now(), old_id),
            )

    def log_label(self, label: str, entity_type: str, entity_id: int, context: str, agent: str | None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO confidence_label_usage(label, entity_type, entity_id, context, agent_id, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (label, entity_type, entity_id, context, self.agent_id(agent), self.clock.now()),
            )

    # ---------- conocimiento ----------
    def upsert_archetype(self, a: Archetype) -> int:
        with self.db:
            self.db.execute(
                "INSERT INTO archetypes(slug, name, definition, rules_json, created_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(slug) DO UPDATE SET name=excluded.name, definition=excluded.definition, "
                "rules_json=excluded.rules_json",
                (a.slug, a.name, a.definition, json.dumps(a.rules, ensure_ascii=False), self.clock.now()),
            )
        return self.find_id("archetype", a.slug)  # type: ignore[return-value]

    def list_archetypes(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM archetypes ORDER BY id")]

    def upsert_instrument(self, i: Instrument) -> int:
        with self.db:
            self.db.execute(
                "INSERT INTO instruments(acronym, name, kind, definition, refutation, related_workstream, "
                "source, agent_id, created_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(acronym) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
                "definition=excluded.definition, refutation=excluded.refutation, "
                "related_workstream=excluded.related_workstream, source=excluded.source",
                (
                    i.acronym,
                    i.name,
                    i.kind,
                    i.definition,
                    i.refutation,
                    i.related_workstream,
                    i.source,
                    self.agent_id(i.agent),
                    self.clock.now(),
                ),
            )
        return self.find_id("instrument", i.acronym)  # type: ignore[return-value]

    def list_instruments(self, workstream: str | None) -> list[dict[str, Any]]:
        if workstream:
            return [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM instruments WHERE related_workstream=? ORDER BY acronym", (workstream,)
                )
            ]
        return [dict(r) for r in self.db.execute("SELECT * FROM instruments ORDER BY related_workstream, acronym")]

    def upsert_skill(self, s: Skill) -> int:
        now = self.clock.now()
        with self.db:
            self.db.execute(
                "INSERT INTO skills(slug, title, path_or_body, version, portable_path, sha256, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET title=excluded.title, "
                "path_or_body=excluded.path_or_body, version=excluded.version, portable_path=excluded.portable_path, "
                "sha256=excluded.sha256, updated_at=excluded.updated_at",
                (s.slug, s.title, s.path_or_body, s.version, s.portable_path, s.sha256, now, now),
            )
        return self.find_id("skill", s.slug)  # type: ignore[return-value]

    def find_id(self, entity_type: str, key: str) -> int | None:
        table, col = _ENTITY_TABLE[entity_type]
        if str(key).isdigit():
            row = self.db.execute(f"SELECT id FROM {table} WHERE id=?", (int(key),)).fetchone()
            if row:
                return row[0]
        if col is None:
            return None
        row = self.db.execute(f"SELECT id FROM {table} WHERE {col}=?", (key,)).fetchone()
        return row[0] if row else None

    def exists(self, entity_type: str, entity_id: int) -> bool:
        table, _ = _ENTITY_TABLE[entity_type]
        return self.db.execute(f"SELECT 1 FROM {table} WHERE id=?", (entity_id,)).fetchone() is not None

    def link_skill(self, skill_id: int, entity_type: str, entity_id: int, note: str) -> int:
        if not self.exists("skill", skill_id) or not self.exists(entity_type, entity_id):
            raise DomainError("skill o entidad destino inexistente")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO skill_links(skill_id, related_entity_type, related_entity_id, note, created_at) "
                "VALUES (?,?,?,?,?)",
                (skill_id, entity_type, entity_id, note, self.clock.now()),
            )
        return self.db.execute(
            "SELECT id FROM skill_links WHERE skill_id=? AND related_entity_type=? AND related_entity_id=?",
            (skill_id, entity_type, entity_id),
        ).fetchone()[0]

    def add_relation(self, r: Relation) -> int:
        for t, i in ((r.from_type, r.from_id), (r.to_type, r.to_id)):
            if not self.exists(t, i):
                raise DomainError(f"{t} {i} no existe")
        now = self.clock.now()
        vf = r.valid_from or now
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO entity_relations(from_type, from_id, relation, to_type, to_id, "
                "note, agent_id, valid_from, valid_until, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r.from_type, r.from_id, r.relation, r.to_type, r.to_id, r.note, self.agent_id(r.agent), vf, r.valid_until, now),
            )
        return self.db.execute(
            "SELECT id FROM entity_relations WHERE from_type=? AND from_id=? AND relation=? AND to_type=? AND to_id=?",
            (r.from_type, r.from_id, r.relation, r.to_type, r.to_id),
        ).fetchone()[0]

    def invalidate_relation(self, relation_id: int, valid_until: str | None = None) -> None:
        row = self.db.execute("SELECT id, valid_from FROM entity_relations WHERE id=?", (relation_id,)).fetchone()
        if row is None:
            raise DomainError(f"relación {relation_id} no existe")
        ts = valid_until or self.clock.now()
        if row["valid_from"] and ts < row["valid_from"]:
            raise DomainError("valid_until < valid_from")
        with self.db:
            self.db.execute("UPDATE entity_relations SET valid_until=? WHERE id=?", (ts, relation_id))

    def neighbors(
        self,
        entity_type: str,
        entity_id: int,
        as_of: str | None = None,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        now = self.clock.now()
        if as_of:
            tfilter = " AND (valid_from IS NULL OR valid_from <= ?) AND (valid_until IS NULL OR valid_until > ?)"
            targs: tuple[Any, ...] = (as_of, as_of)
        elif not include_expired:
            tfilter = " AND (valid_until IS NULL OR valid_until > ?)"
            targs = (now,)
        else:
            tfilter = ""
            targs = ()
        sql = (
            "SELECT id AS rel_id, 'out' AS dir, relation, to_type AS type, to_id AS id, note, valid_from, valid_until "
            f"FROM entity_relations WHERE from_type=? AND from_id=?{tfilter} "
            "UNION ALL "
            "SELECT id AS rel_id, 'in' AS dir, relation, from_type AS type, from_id AS id, note, valid_from, valid_until "
            f"FROM entity_relations WHERE to_type=? AND to_id=?{tfilter} "
            "UNION ALL "
            "SELECT id AS rel_id, 'skill' AS dir, 'linked' AS relation, 'skill' AS type, skill_id AS id, note, "
            "created_at AS valid_from, NULL AS valid_until "
            "FROM skill_links WHERE related_entity_type=? AND related_entity_id=?"
        )
        args = (entity_type, entity_id, *targs, entity_type, entity_id, *targs, entity_type, entity_id)
        return [dict(r) for r in self.db.execute(sql, args)]

    # ---------- portabilidad / snapshot ----------
    def export_all(self) -> dict[str, list[dict[str, Any]]]:
        return {t: [dict(r) for r in self.db.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in TABLES_IN_ORDER}

    def import_all(self, data: dict[str, Iterable[dict[str, Any]]], mode: str = "merge") -> dict[str, int]:
        if mode not in ("merge", "replace"):
            raise DomainError("mode debe ser merge o replace")
        counts: dict[str, int] = {}
        cols_of = {t: [r[1] for r in self.db.execute(f"PRAGMA table_info({t})")] for t in TABLES_IN_ORDER}
        with self.db:
            self.db.execute("PRAGMA defer_foreign_keys = ON")
            if mode == "replace":
                for t in reversed(TABLES_IN_ORDER):
                    self.db.execute(f"DELETE FROM {t}")
            for t in TABLES_IN_ORDER:
                n = 0
                for row in data.get(t, []) or []:
                    row = {k: v for k, v in row.items() if k in cols_of[t]}
                    if "created_at" in cols_of[t] and not row.get("created_at"):
                        row["created_at"] = self.clock.now()
                    if t == "skills" and not row.get("updated_at"):
                        row["updated_at"] = self.clock.now()
                    if t == "memory_facts" and not row.get("valid_from"):
                        row["valid_from"] = self.clock.now()
                    if t == "memory_facts":
                        MemoryFact(
                            type=row.get("type", ""),
                            fact_text=row.get("fact_text", ""),
                            confidence=(
                                float(row["confidence"]) if row.get("confidence") is not None else None
                            ),
                            evidence_label=row.get("evidence_label"),
                            meta_json=row.get("meta_json", "{}"),
                            source_key=row.get("source_key"),
                        )
                    keys = list(row)
                    cur = self.db.execute(
                        f"INSERT OR IGNORE INTO {t}({','.join(keys)}) VALUES ({','.join('?' * len(keys))})",
                        [row[k] for k in keys],
                    )
                    n += cur.rowcount
                counts[t] = n
        return counts

    def backup_to(self, dest_path: str) -> str:
        dst = Path(dest_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        out_conn = sqlite3.connect(str(dst), timeout=30)
        try:
            self.db.backup(out_conn)
            ok = out_conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise DomainError(f"backup falló integrity_check: {ok}")
        finally:
            out_conn.close()
        return str(dst)
