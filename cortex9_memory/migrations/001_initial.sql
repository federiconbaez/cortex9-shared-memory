-- 001_initial: esquema base de cortex9-shared-memory
PRAGMA foreign_keys = ON;

CREATE TABLE agents (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE CHECK (name GLOB '[a-z0-9]*' AND name NOT GLOB '*[^a-z0-9-]*'),
  role        TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);

CREATE TABLE sessions (
  id          INTEGER PRIMARY KEY,
  agent_id    INTEGER REFERENCES agents(id) ON DELETE SET NULL,
  channel     TEXT NOT NULL DEFAULT 'chat',
  title       TEXT NOT NULL DEFAULT '',
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  summary     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE message_summaries (
  id          INTEGER PRIMARY KEY,
  session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  turn_range  TEXT NOT NULL,
  summary     TEXT NOT NULL CHECK (length(trim(summary)) > 0),
  tokens_est  INTEGER NOT NULL DEFAULT 0 CHECK (tokens_est >= 0),
  agent_id    INTEGER REFERENCES agents(id) ON DELETE SET NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE memory_types (
  slug        TEXT PRIMARY KEY,
  description TEXT NOT NULL
);

CREATE TABLE confidence_labels (
  slug        TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  definition  TEXT NOT NULL,
  min_score   REAL NOT NULL,
  max_score   REAL NOT NULL
);

CREATE TABLE memory_facts (
  id             INTEGER PRIMARY KEY,
  type           TEXT NOT NULL REFERENCES memory_types(slug),
  fact_text      TEXT NOT NULL CHECK (length(trim(fact_text)) > 0),
  meta_json      TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta_json)),
  confidence     REAL NOT NULL DEFAULT 0.8 CHECK (confidence BETWEEN 0 AND 1),
  evidence_label TEXT REFERENCES confidence_labels(slug),
  valid_from     TEXT NOT NULL,
  valid_until    TEXT CHECK (valid_until IS NULL OR valid_until >= valid_from),
  source         TEXT NOT NULL DEFAULT '',
  agent_id       INTEGER REFERENCES agents(id) ON DELETE SET NULL,
  session_id     INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  superseded_by  INTEGER REFERENCES memory_facts(id),
  created_at     TEXT NOT NULL
);
CREATE INDEX ix_facts_type ON memory_facts(type);
CREATE INDEX ix_facts_live ON memory_facts(superseded_by, valid_until);

CREATE VIRTUAL TABLE memory_facts_fts USING fts5(
  fact_text, source, meta_json, content='memory_facts', content_rowid='id',
  tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER memory_facts_ai AFTER INSERT ON memory_facts BEGIN
  INSERT INTO memory_facts_fts(rowid, fact_text, source, meta_json) VALUES (new.id, new.fact_text, new.source, new.meta_json);
END;
CREATE TRIGGER memory_facts_ad AFTER DELETE ON memory_facts BEGIN
  INSERT INTO memory_facts_fts(memory_facts_fts, rowid, fact_text, source, meta_json) VALUES ('delete', old.id, old.fact_text, old.source, old.meta_json);
END;
CREATE TRIGGER memory_facts_au AFTER UPDATE ON memory_facts BEGIN
  INSERT INTO memory_facts_fts(memory_facts_fts, rowid, fact_text, source, meta_json) VALUES ('delete', old.id, old.fact_text, old.source, old.meta_json);
  INSERT INTO memory_facts_fts(rowid, fact_text, source, meta_json) VALUES (new.id, new.fact_text, new.source, new.meta_json);
END;

CREATE TABLE archetypes (
  id          INTEGER PRIMARY KEY,
  slug        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  definition  TEXT NOT NULL,
  rules_json  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(rules_json)),
  created_at  TEXT NOT NULL
);

CREATE TABLE instruments (
  id                  INTEGER PRIMARY KEY,
  acronym             TEXT NOT NULL UNIQUE,
  name                TEXT NOT NULL,
  kind                TEXT NOT NULL CHECK (kind IN ('T','R','I','F')),
  definition          TEXT NOT NULL CHECK (length(trim(definition)) > 0),
  refutation          TEXT NOT NULL CHECK (length(trim(refutation)) > 0),
  related_workstream  TEXT NOT NULL DEFAULT '',
  source              TEXT NOT NULL DEFAULT '',
  agent_id            INTEGER REFERENCES agents(id) ON DELETE SET NULL,
  created_at          TEXT NOT NULL,
  CHECK (substr(acronym,1,1) = kind)
);

CREATE TABLE skills (
  id             INTEGER PRIMARY KEY,
  slug           TEXT NOT NULL UNIQUE,
  title          TEXT NOT NULL,
  path_or_body   TEXT NOT NULL,
  version        TEXT NOT NULL DEFAULT '1',
  portable_path  TEXT NOT NULL DEFAULT '',
  sha256         TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE skill_links (
  id                   INTEGER PRIMARY KEY,
  skill_id             INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  related_entity_type  TEXT NOT NULL CHECK (related_entity_type IN ('agent','session','summary','fact','archetype','instrument','skill')),
  related_entity_id    INTEGER NOT NULL,
  note                 TEXT NOT NULL DEFAULT '',
  created_at           TEXT NOT NULL,
  UNIQUE (skill_id, related_entity_type, related_entity_id)
);

CREATE TABLE entity_relations (
  id          INTEGER PRIMARY KEY,
  from_type   TEXT NOT NULL CHECK (from_type IN ('agent','session','summary','fact','archetype','instrument','skill')),
  from_id     INTEGER NOT NULL,
  relation    TEXT NOT NULL CHECK (length(trim(relation)) > 0),
  to_type     TEXT NOT NULL CHECK (to_type IN ('agent','session','summary','fact','archetype','instrument','skill')),
  to_id       INTEGER NOT NULL,
  note        TEXT NOT NULL DEFAULT '',
  agent_id    INTEGER REFERENCES agents(id) ON DELETE SET NULL,
  created_at  TEXT NOT NULL,
  UNIQUE (from_type, from_id, relation, to_type, to_id)
);
CREATE INDEX ix_rel_from ON entity_relations(from_type, from_id);
CREATE INDEX ix_rel_to ON entity_relations(to_type, to_id);

CREATE TABLE confidence_label_usage (
  id          INTEGER PRIMARY KEY,
  label       TEXT NOT NULL REFERENCES confidence_labels(slug),
  entity_type TEXT NOT NULL,
  entity_id   INTEGER NOT NULL,
  context     TEXT NOT NULL DEFAULT '',
  agent_id    INTEGER REFERENCES agents(id) ON DELETE SET NULL,
  created_at  TEXT NOT NULL
);
