-- 003_codebook: alias compartidos entre bots (F02, codebook sin pérdida)
CREATE TABLE codebook_aliases (
  name        TEXT PRIMARY KEY CHECK (length(name) BETWEEN 1 AND 16 AND name NOT GLOB '*[^A-Za-z0-9_]*'),
  expansion   TEXT NOT NULL UNIQUE CHECK (length(expansion) >= 8 AND instr(expansion, '@') = 0
                                          AND instr(expansion, '{') = 0 AND instr(expansion, '}') = 0),
  agent_id    INTEGER REFERENCES agents(id) ON DELETE SET NULL,
  created_at  TEXT NOT NULL
);
