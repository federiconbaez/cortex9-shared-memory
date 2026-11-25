# cortex9-shared-memory (`c9mem`)

Almacén **SQLite** de memoria compartida bajo control de Federico Nicolas Baez Rizzo, para múltiples agentes en una misma máquina y para Grok fuera de los bots (vía export/import JSON). Guarda hechos de memoria con decay por recencia y uso, sesiones y resúmenes de conversación, arquetipos autorales, instrumentos analíticos (`T*/R*/I*/F*-FNBR`), skills y un grafo temporal de relaciones.

Es **paralelo** a la memoria oficial de cualquier producto: no la lee ni la escribe. Ver [CONTRACT.md](CONTRACT.md).

## Instalación y rutas

Solo usa la biblioteca estándar de Python (≥3.10, SQLite con FTS5).

```bash
pip install -e .                 # opcional, o ejecutar: python -m cortex9_memory
pytest -q                        # tests de dominio + integración SQLite
```

Variables de entorno:

| Variable | Uso | Default |
|---|---|---|
| `C9MEM_DB` | Ruta del archivo SQLite | `./data/c9mem.sqlite` |
| `C9MEM_AGENT` | Agente activo que firma escrituras | `None` |
| `C9MEM_WORKFLOWS_DIR` | Carpeta con `*/SKILL.md` para `skill-sync` | `""` |
| `C9MEM_BACKUP_DIR` | Destino de `c9mem backup` | `./backups` |

## Uso rápido

```bash
export C9MEM_AGENT=primary
c9mem init                                        # migraciones + seeds estructurales (idempotente)
c9mem import-json examples/seed-example.json      # seed de ejemplo opcional

# Hechos con clave idempotente (multi-agente seguro) y búsqueda con decay + punto en el tiempo
c9mem fact-add behavior "Marcar NO ENCONTRADO en vez de inventar" --label alta --source-key "rule:no-inventar"
c9mem fact-add workstream "Repo X publicado con CI verde" --confidence 0.95 --meta '{"repo":"x"}'
c9mem fact-search "inventar" --touch              # FTS5 + confianza + decay temporal + uso
c9mem fact-search --as-of 2026-11-24T00:00:00Z    # qué era cierto en esa fecha
c9mem fact-supersede 1 2                          # el 1 queda reemplazado por el 2 (no se borra)

# Context Pack para inyectar en un prompt respetando presupuesto de tokens
c9mem context-pack --agent primary --budget 600 --query "repo"

# Sesiones y resúmenes
S=$(c9mem session-start --channel chat --title "Investigación X")
c9mem session-summarize-add $S t1-t12 "Resumen del tramo…"

# Arquetipos, instrumentos y skills
c9mem archetype-list
c9mem instrument-add TEME-FNBR "Teorema del Eslabón Mínimo" "definición…" --refutation "se refuta si…" --workstream tesis-x
c9mem instrument-harvest ./tesis.md --workstream tesis-x
c9mem skill-sync --dir ./skills-dir --link-archetypes

# Grafo temporal de relaciones
c9mem relate instrument TEME-FNBR instance_of archetype constructor-de-instrumentos --valid-from 2026-11-01T00:00:00Z
c9mem neighbors instrument TEME-FNBR --as-of 2026-11-15T00:00:00Z
c9mem relation-invalidate 1

# Portabilidad, salud y backup
c9mem export-json --tables memory_types,confidence_labels,archetypes,instruments,skills,memory_facts,message_summaries,entity_relations --out exports/grok.json
c9mem import-json exports/grok-editado.json --mode merge
c9mem backup --dir ./backups --keep 14
c9mem doctor
c9mem stats
```

## Modelo y arquitectura

`agents`, `sessions`, `message_summaries`, `memory_types` (`world` · `user_profile` · `behavior` · `workstream`), `memory_facts` (`+ memory_facts_fts`, `source_key` único parcial, `access_count`, `last_accessed_at`), `confidence_labels` (`alta` · `media` · `estimacion` · `no_encontrado`), `confidence_label_usage`, `archetypes`, `instruments`, `skills`, `skill_links`, `entity_relations` (bi-temporal: `valid_from`, `valid_until`), `schema_migrations`.

Arquitectura hexagonal: `domain/` (`models.py`, `ports.py`, `harvest.py`, `scoring.py`; sin `sqlite3`) → `application/service.py` → `adapters/sqlite_repo.py`. Las invariantes se validan en el dominio y también con `CHECK` / `FOREIGN KEY` en SQLite.

## Concurrencia multi-agente

- Modo `WAL` + `busy_timeout = 30000 ms`: varios agentes locales pueden leer y escribir sin bloquearse.
- `--source-key`: permite re-ejecutar o escribir descubrimientos en paralelo con upsert idempotente en lugar de duplicar filas.
- Nunca guardar contraseñas, tokens ni datos de tarjetas.

## Backups y rotación (cron o vault)

`c9mem backup --dir "$C9MEM_BACKUP_DIR" --keep 14` usa la API nativa `sqlite3.Connection.backup()` (segura con escrituras concurrentes en WAL), corre `PRAGMA integrity_check` sobre el snapshot y borra los más antiguos dejando `keep` copias.

Ejemplo para `cron` (diario a las 03:15 UTC) o paso previo a una sincronización de vault:

```cron
15 3 * * * C9MEM_DB=/ruta/data/c9mem.sqlite c9mem backup --dir /ruta/backups --keep 14 >/dev/null
```

Para sincronizar hacia un vault/rsync remoto, copiá el archivo generado en `/ruta/backups/` (nunca copies la `.sqlite` viva mientras haya `-wal` sin checkpoint).

## Cómo lo usa Grok (fuera de los bots)

1. `c9mem export-json --tables memory_types,confidence_labels,archetypes,instruments,skills,memory_facts,message_summaries,entity_relations --out exports/grok.json`
2. Adjuntar en Grok [GROK_PORTABLE.md](GROK_PORTABLE.md) + `exports/grok.json`.
3. Grok devuelve filas nuevas en el mismo formato JSON (sin `id`).
4. `c9mem import-json respuesta.json --mode merge` (transaccional: valida todas las filas antes de confirmar).

## Licencia

Apache-2.0. © Federico Nicolas Baez Rizzo.
