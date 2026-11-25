# CONTRACT — cortex9-shared-memory (c9mem)

Almacén de memoria compartida, en SQLite, bajo control de Federico Nicolas Baez Rizzo. Lo usan múltiples agentes en una misma máquina y Grok fuera de los bots (vía export/import JSON). Es un almacén **paralelo**: no reemplaza ni escribe en la memoria oficial de ningún producto.

## Entidades

| Entidad | Identidad | Descripción |
|---|---|---|
| Agent | `name` único | Agente o asistente que escribe/lee. |
| Session | `id` | Conversación de un agente en un canal (`chat`, `slack`, `grok`, `routine`…), con resumen opcional. |
| MessageSummary | `id` | Resumen de un rango de turnos (`t269u-t281u`) dentro de una sesión. |
| MemoryType | `slug` | `world`, `user_profile`, `behavior`, `workstream` (cerrado). |
| MemoryFact | `id` (`source_key` opcional único) | Hecho atómico con tipo, texto, confianza 0..1, etiqueta de evidencia, vigencia, fuente, `access_count` y `last_accessed_at`. |
| ConfidenceLabel | `slug` | `alta`, `media`, `estimacion`, `no_encontrado` (escala del protocolo doctoral). |
| Archetype | `slug` | Arquetipo autoral (los 4 del ADN doctoral FNBR). |
| Instrument | `acronym` único | Teorema/Regla/Índice/Framework (`T`/`R`/`I`/`F`) con definición y criterio de refutación. |
| Skill | `slug` | Skill/protocolo: ruta o cuerpo, versión, ruta portable (Grok). |
| SkillLink | (skill, tipo, id) | Vínculo de un skill con otra entidad. |
| EntityRelation | (from, relation, to) | Grafo bi-temporal (`valid_from`, `valid_until`) entre cualquier par de entidades. |

## Invariantes

1. `MemoryFact.type ∈ {world, user_profile, behavior, workstream}`; `fact_text` no vacío; `0 ≤ confidence ≤ 1`.
2. `valid_until`, si existe, es ≥ `valid_from`. Un hecho vencido no se borra: se **supersede** (`superseded_by`) o se cierra con `valid_until`. La búsqueda por defecto devuelve solo hechos vigentes; `--as-of` consulta lo que era cierto en una fecha dada.
3. `source_key`, si se provee, es no vacío y único: escribir con el mismo `source_key` hace upsert idempotente (evita duplicados bajo concurrencia multi-agente).
4. `meta_json` y `rules_json` deben ser JSON válido (objeto/array).
5. `Instrument.kind ∈ {T, R, I, F}` y coincide con la primera letra del acrónimo; `definition` y `refutation` no vacíos (si falta, el texto es literalmente `NO ENCONTRADO`).
6. `Archetype.slug`, `Skill.slug`, `Agent.name` en kebab-case minúscula `[a-z0-9-]+`.
7. `EntityRelation` y `SkillLink` solo apuntan a tipos de entidad conocidos: `agent, session, summary, fact, archetype, instrument, skill`. Se valida existencia del destino al insertar; `EntityRelation.valid_until >= valid_from`.
8. Cada migración se aplica una sola vez y queda en `schema_migrations` con su checksum SHA-256; si un archivo ya aplicado cambia, `doctor` falla.
9. Prohibido inventar datos personales: los seeds de repositorio solo contienen estructura (tipos, etiquetas, arquetipos, agentes genéricos). Los hechos los cargan los agentes con su fuente.
10. Toda escritura registra `agent_id` (o NULL si es humano/CLI sin agente) y `created_at` en UTC ISO-8601.

## Puertos (domain/ports.py)

- `MemoryRepository`: agentes, sesiones, resúmenes, hechos (add/upsert, search con ranking híbrido y `--as-of`, supersede, touch), arquetipos, instrumentos, skills, links, relaciones temporales (add, invalidate, neighbors `--as-of`), labels de confianza, export/import y `backup_to`.
- `Clock`: `now()` en UTC (inyectable en tests).

Adaptadores: `adapters/sqlite_repo.py` (SQLite WAL + `busy_timeout=30000` + FTS5 + migraciones). El dominio no importa `sqlite3`.

## Casos de uso (application/service.py)

`init`, `migrate`, `fact-add` (con `--source-key`), `fact-search` (ranking híbrido FTS5 + confianza + decay temporal + uso, `--as-of`, `--touch`), `fact-supersede`, `session-start`, `session-summarize-add`, `archetype-list`, `instrument-add`, `instrument-harvest`, `skill-register`, `skill-sync` (solo lectura de `*/SKILL.md`), `link`, `relate` (con vigencia temporal), `relation-invalidate`, `neighbors` (`--as-of`), `context-pack` (presupuesto de tokens; no incrementa `access_count` salvo `--touch`), `backup` (snapshot con rotación y campo `integrity`), `export-json`, `import-json`, `doctor`, `stats`.

## Fuera de alcance

- Sincronizar con la memoria oficial de un producto externo.
- Embeddings / búsqueda semántica (FTS5 léxico + decay temporal; un adaptador vectorial puede añadirse luego).
- Concurrencia multi-host: es un archivo local con WAL; varios agentes en la misma máquina sí, varias máquinas compartiendo disco de red no.
- Cifrado en reposo y secretos: **no guardar credenciales ni tokens** aquí.
