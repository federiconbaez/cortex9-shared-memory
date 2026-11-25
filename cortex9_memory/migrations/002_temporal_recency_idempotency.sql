-- 002_temporal_recency_idempotency: decay por recencia/uso, grafo temporal y source_key idempotente
ALTER TABLE memory_facts ADD COLUMN source_key TEXT;
ALTER TABLE memory_facts ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0 CHECK (access_count >= 0);
ALTER TABLE memory_facts ADD COLUMN last_accessed_at TEXT;

CREATE UNIQUE INDEX ix_facts_source_key ON memory_facts(source_key) WHERE source_key IS NOT NULL;

ALTER TABLE entity_relations ADD COLUMN valid_from TEXT;
ALTER TABLE entity_relations ADD COLUMN valid_until TEXT CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from);
