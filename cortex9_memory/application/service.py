"""Casos de uso. Orquesta dominio + puerto; no conoce SQLite."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from ..domain.harvest import harvest
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
    require_json,
    require_slug,
)
from ..domain.ports import MemoryRepository
from ..domain.scoring import pack_context, pack_context_knapsack
from ..domain.taint import Verdict, assess, enforce_fact_write, parse_tier
from . import seed

EXPORT_FORMAT = "cortex9-shared-memory/v1"
_FM_NAME = re.compile(r"^name:\s*(.+)$", re.M)
_H1 = re.compile(r"^#\s+(.+)$", re.M)


def _parse_skill_title(body: str, fallback: str) -> str:
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end > 0:
            fm = body[3:end]
            m = _FM_NAME.search(fm)
            if m:
                return m.group(1).strip().strip('"\'')
    m = _H1.search(body)
    return m.group(1).strip() if m else fallback


class MemoryService:
    def __init__(self, repo: MemoryRepository) -> None:
        self.repo = repo

    # esquema
    def init(self, workflows_dir: str | None = None) -> dict[str, Any]:
        applied = self.repo.migrate()
        self.seed_structural(workflows_dir=workflows_dir)
        return {"applied": applied, "stats": self.repo.stats()}

    def seed_structural(self, workflows_dir: str | None = None) -> None:
        self.repo.import_all(
            {
                "memory_types": [{"slug": s, "description": d} for s, d in seed.MEMORY_TYPES],
                "confidence_labels": [
                    {"slug": s, "name": n, "definition": d, "min_score": lo, "max_score": hi}
                    for s, n, d, lo, hi in seed.CONFIDENCE_LABELS
                ],
            },
            mode="merge",
        )
        for name, role in seed.AGENTS:
            self.repo.ensure_agent(Agent(name, role))
        arch_ids = [self.repo.upsert_archetype(Archetype(s, n, d, r)) for s, n, d, r in seed.ARCHETYPES]
        portable = Path(__file__).resolve().parents[2] / "skills-portable" / "grok-skill-investigacion-propia-fnbr.md"
        if portable.exists():
            sid = self.register_skill(
                "investigacion-propia-fnbr-portable",
                "Investigación Propia Doctoral FNBR (Portable para Grok)",
                str(portable),
                portable_path=str(portable),
            )
            for aid in arch_ids:
                self.repo.link_skill(sid, "archetype", aid, "aplica el arquetipo")
        wf = workflows_dir or os.environ.get("C9MEM_WORKFLOWS_DIR")
        if wf and Path(wf).is_dir():
            self.sync_skills(wf, link_archetypes=True)

    # hechos
    def add_fact(self, trust_tier: str = "T2", tainted: bool = False, **kw: Any) -> int:
        """Persiste un hecho tras el taint-guard (I-TAINT). Tier y taint quedan en meta_json."""
        tier = parse_tier(trust_tier)
        enforce_fact_write(kw.get("type", ""), str(kw.get("fact_text") or ""), tier, tainted)
        meta = json.loads(require_json(kw.get("meta_json", "{}"), "meta_json"))
        if isinstance(meta, dict):
            meta["_trust"] = {"tier": tier.name, "tainted": bool(tainted)}
            kw["meta_json"] = meta
        fact = MemoryFact(**kw)
        fid = self.repo.add_fact(fact)
        self.repo.log_label(fact.resolved_label(), "fact", fid, "fact-add", fact.agent)
        return fid

    def search(
        self,
        query: str | None = None,
        type_: str | None = None,
        include_expired: bool = False,
        limit: int = 20,
        as_of: str | None = None,
        touch: bool = False,
        half_life_days: float = 30.0,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise DomainError("limit >= 1")
        return self.repo.search_facts(query, type_, include_expired, limit, as_of, touch, half_life_days)

    def taint_check(self, text: str, trust_tier: str = "T1") -> Verdict:
        return assess(text, parse_tier(trust_tier))

    def supersede(self, old_id: int, new_id: int) -> None:
        if old_id == new_id:
            raise DomainError("un hecho no puede supersederse a sí mismo")
        self.repo.supersede_fact(old_id, new_id)

    # sesiones
    def start_session(self, agent: str | None, channel: str, title: str, summary: str = "") -> int:
        return self.repo.add_session(Session(agent, channel, title, summary))

    def add_summary(
        self,
        session_id: int,
        turn_range: str,
        summary: str,
        tokens_est: int | None = None,
        agent: str | None = None,
    ) -> int:
        tokens = tokens_est if tokens_est is not None else MessageSummary.estimate_tokens(summary)
        return self.repo.add_summary(MessageSummary(session_id, turn_range, summary, tokens, agent))

    # conocimiento
    def add_instrument(self, **kw: Any) -> int:
        return self.repo.upsert_instrument(Instrument(**kw))

    def harvest_instruments(
        self,
        md_path: str,
        workstream: str,
        agent: str | None = None,
        archetype_slug: str = "constructor-de-instrumentos",
    ) -> list[str]:
        text = Path(md_path).read_text(encoding="utf-8")
        arch_id = self.repo.find_id("archetype", archetype_slug)
        out: list[str] = []
        for h in harvest(text):
            iid = self.repo.upsert_instrument(
                Instrument(h.acronym, h.name, h.kind, h.definition, h.refutation, workstream, md_path, agent)
            )
            if arch_id:
                self.repo.add_relation(Relation("instrument", iid, "instance_of", "archetype", arch_id))
            out.append(h.acronym)
        return out

    def register_skill(
        self,
        slug: str,
        title: str,
        path_or_body: str,
        version: str | None = None,
        portable_path: str = "",
    ) -> int:
        p = Path(path_or_body)
        body = p.read_text(encoding="utf-8") if p.exists() else path_or_body
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return self.repo.upsert_skill(Skill(slug, title, path_or_body, version or sha[:12], portable_path, sha))

    def sync_skills(self, workflows_dir: str, link_archetypes: bool = False) -> list[dict[str, str]]:
        root = Path(workflows_dir)
        if not root.is_dir():
            raise DomainError(f"directorio de skills inexistente: {workflows_dir}")
        synced: list[dict[str, str]] = []
        for skill_md in sorted(root.glob("*/SKILL.md")):
            slug = skill_md.parent.name.lower().replace("_", "-")
            require_slug(slug, "skill.slug")
            body = skill_md.read_text(encoding="utf-8")
            title = _parse_skill_title(body, slug)
            sid = self.register_skill(slug, title, str(skill_md))
            if link_archetypes:
                for arch in self.repo.list_archetypes():
                    self.repo.link_skill(sid, "archetype", int(arch["id"]), "skill-sync")
            synced.append({"slug": slug, "title": title, "path": str(skill_md)})
        return synced

    def link(self, skill: str, entity_type: str, entity: str, note: str = "") -> int:
        sid = self.repo.find_id("skill", skill)
        eid = self.repo.find_id(entity_type, entity)
        if sid is None or eid is None:
            raise DomainError(f"no encontrado: skill={skill} {entity_type}={entity}")
        return self.repo.link_skill(sid, entity_type, eid, note)

    def relate(
        self,
        from_type: str,
        from_key: str,
        relation: str,
        to_type: str,
        to_key: str,
        note: str = "",
        agent: str | None = None,
        valid_from: str | None = None,
        valid_until: str | None = None,
    ) -> int:
        fid, tid = self.repo.find_id(from_type, from_key), self.repo.find_id(to_type, to_key)
        if fid is None or tid is None:
            raise DomainError(f"no encontrado: {from_type}={from_key} o {to_type}={to_key}")
        return self.repo.add_relation(
            Relation(from_type, fid, relation, to_type, tid, note, agent, valid_from, valid_until)
        )

    def invalidate_relation(self, relation_id: int, valid_until: str | None = None) -> None:
        self.repo.invalidate_relation(relation_id, valid_until)

    def context_pack(
        self,
        agent: str,
        budget_tokens: int = 800,
        query: str | None = None,
        touch: bool = False,
        strategy: str = "knapsack",
    ) -> dict[str, Any]:
        if strategy not in ("knapsack", "greedy"):
            raise DomainError("strategy debe ser knapsack o greedy")
        if budget_tokens < 40:
            raise DomainError("budget_tokens debe ser >= 40")
        behaviors = self.repo.search_facts(None, "behavior", False, 15, None, False)
        workstreams = self.repo.search_facts(query, "workstream", False, 10, None, False)
        matches = self.repo.search_facts(query, None, False, 12, None, False) if query else []
        summaries = self.repo.recent_summaries(agent=agent, limit=5)
        instruments = self.repo.list_instruments(None)
        if query:
            ql = query.lower()
            instruments = [
                i for i in instruments if ql in i["acronym"].lower() or ql in i["name"].lower() or ql in i["related_workstream"].lower()
            ]
        packer = pack_context_knapsack if strategy == "knapsack" else pack_context
        pack = packer(agent, budget_tokens, behaviors, workstreams, matches, summaries, instruments[:8])
        # Por defecto no toca access_count. Con touch=True incrementa una sola vez
        # por hecho realmente incluido (ya deduplicado en pack["fact_ids"]).
        if touch and pack["fact_ids"]:
            self.repo.touch_facts(pack["fact_ids"])
        return pack

    def backup(self, backup_dir: str, now_iso: str, keep: int = 14) -> dict[str, Any]:
        if keep < 1:
            raise DomainError("keep debe ser >= 1")
        stamp = now_iso.replace("-", "").replace(":", "").replace("T", "-")
        out_dir = Path(backup_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"c9mem-{stamp}.sqlite"
        saved, integrity = self.repo.backup_to(str(dest))
        snaps = sorted(out_dir.glob("c9mem-*.sqlite"))
        removed: list[str] = []
        while len(snaps) > keep:
            victim = snaps.pop(0)
            victim.unlink()
            removed.append(str(victim))
        return {"backup": saved, "kept": len(snaps), "removed": removed, "integrity": integrity}

    # portabilidad
    def export(self, now: str) -> dict[str, Any]:
        return {
            "format": EXPORT_FORMAT,
            "exported_at": now,
            "conventions": {
                "memory_types": [s for s, _ in seed.MEMORY_TYPES],
                "evidence_labels": [s for s, *_ in seed.CONFIDENCE_LABELS],
                "instrument_kinds": ["T", "R", "I", "F"],
                "times": "UTC ISO-8601 (Z)",
            },
            "tables": self.repo.export_all(),
        }

    def import_(self, payload: dict[str, Any], mode: str = "merge") -> dict[str, int]:
        if payload.get("format") != EXPORT_FORMAT:
            raise DomainError(f"formato desconocido: {payload.get('format')!r}")
        return self.repo.import_all(payload["tables"], mode)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
