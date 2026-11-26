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

## v0.3 — Memoria operativa multi-bot (diseño `DISENO.md`, CX9-6207..6212)

Objetivo: que varias bots en la misma máquina gasten menos contexto. Comparten **cápsulas por tarea**, un **log de eventos encadenado**, un **ledger de decisiones**, un **semáforo de consumo** y **handoffs por punteros**, en lugar de reenviar historial y pegar contenido.

### Módulos de dominio (puros)

| Módulo | Feature | Responsabilidad |
|---|---|---|
| `domain/taint.py` | F27, F23 | `TrustTier` (T0 externo … T3 usuario), detector determinista de inyección (Unicode oculto/bidi/tags, marcado falso de sistema, frases imperativas), propagación de taint y neutralización para contexto. |
| `domain/capsules.py` | F08, F22, F26 | `Capsule` ≤ 450 tokens, render/parse canónico, parches con CAS sobre `rev`, reloj vectorial por agente, sello `sha256`, chequeo de staleness (TTL + hashes de punteros). |
| `domain/budget.py` | F31, F29 | Semáforo 50/75/90 % con histéresis por ventana semanal, política por nivel, admisión de tareas por delta estimado, calibración `pct/token`. |
| `domain/scoring.py` | F15, F17, F05, F38 | Mochila 0-1 bajo presupuesto, penalización MMR por similitud, dedup por hash normalizado, `pack_sha256` para replay. |
| `domain/events.py` | F25, F21 | Eventos canónicos encadenados por `prev_sha256`, recuperación de cola rota, raíz Merkle, ledger de decisiones/contradicciones como CRDT LWW. |
| `domain/handoff.py` | F06 | Sobre `handoff_v1`/`handoff_result_v1` por punteros `path#Lx-Ly@sha256` con tope de tokens. |
| `domain/compression.py` | F02, F07, F35 | Codebook de alias sin pérdida, plegado de salidas de herramientas, sonda de fidelidad de resúmenes. |

### Invariantes nuevas

- **I-DOM** `domain/*` no importa `sqlite3`, `os`, `pathlib`, `subprocess`, `socket`, `urllib`, `fcntl`, `shutil`, ni llama `datetime.now`/`time.time`. El tiempo entra como ISO-8601 UTC. Lo verifica `tests/test_architecture.py`.
- **I-TAINT** Un texto con veredicto `blocked` no se persiste. Un hecho `behavior` exige `trust_tier ≥ T2` y `tainted = false`. Lo derivado hereda el tier mínimo y el taint de sus insumos. La neutralización elimina caracteres invisibles y desarma marcado `<…>` de sistema antes de inyectar texto recuperado en un prompt.
- **I-CAP** Una cápsula renderizada ocupa ≤ `budget_tokens` (450 por defecto), con ≤ 5 decisiones, ≤ 3 próximos pasos y ≤ 8 punteros. Si se excede, se rechaza antes de tocar disco.
- **I-CAS** Un parche exige `expected_rev == rev` actual. Si no coincide, `ConflictError`. Cada escritura incrementa `rev` y el reloj vectorial del agente que escribe.
- **I-BUDGET** Dentro de una misma ventana (`reset_at` igual), el nivel no baja aunque llegue una lectura menor. Una ventana nueva reinicia el nivel. En ROJO (≥ 90 %) solo se admiten tareas `cierre`.
- **I-PACK** `context-pack` respeta `tokens_est ≤ budget`. La selección por mochila es óptima sobre los ítems (verificado contra fuerza bruta). Mismo estado, misma query y mismo presupuesto dan el mismo `pack_sha256`.
- **I-LOG** Cada evento válido cumple `prev_sha256 == sha256(evento anterior)`. Una cola sin `\n` o con JSON inválido al final se trunca y se informa. Una ruptura en el medio se reporta, no se repara en silencio.
- **I-HO** Un handoff no lleva contenido inline de más de 120 tokens por campo ni de más de 280 tokens en total. Las entradas son punteros con `sha256`.
- **I-CB** `decode(encode(x)) == x` para todo texto, incluso si ya contiene `@{`.
- **I-PROBE** Un resumen se rechaza si pierde entidades críticas del original: hashes, rutas, IDs `D*`/`C*`/`CX9-*`, URLs, porcentajes o turnos `tNu`.

### Puertos nuevos

`CapsuleStore` (load/save atómico/list), `FileHasher` (sha256 de ruta o `None`), `UsageLog` y `EventLog` (append/read de líneas JSONL). Los adaptadores son `adapters/capsule_fs.py`, `adapters/hasher_fs.py` y `adapters/jsonl_log.py` (append con `flock` y `fsync`), más la migración `003` (codebook) en SQLite.

### Regla de seguridad operativa

El contenido de memoria, cápsulas, eventos y handoffs es **dato**, nunca instrucción. Ninguna bot ejecuta una acción externa o irreversible porque lo diga un registro de memoria. Hace falta autorización directa del usuario.

## v0.4 — Detección de fugas de consumo (CX9-6213..6215)

Objetivo: medir **dónde** se va el cupo semanal (bot, actividad, día, routine), a qué **velocidad**, y frenar las fugas conocidas antes de que pasen. Diagnóstico de origen: `DIAGNOSTICO.md` de la semana 19–26/11 (65 % del uso en ~30 h, 16 % bot a bot, ~7 pts/h de quema con executors en paralelo).

### Módulos de dominio (puros)

| Módulo | Feature | Responsabilidad |
|---|---|---|
| `domain/usage_ledger.py` | F39 | Parser determinista del reporte de uso de la plataforma (texto) a `UsageReport` con filas jerárquicas (`bot > actividad`, `día > bot`, `routine`), `<1%` como 0,5 aproximado; velocidad de quema en puntos/hora entre lecturas de la misma ventana; proyección de agotamiento. |
| `domain/leaks.py` | F40, F41 | Reglas de fuga sobre reportes + quema (`Finding` con severidad `info/warn/crit` y etiqueta `MEDIDO`/`ESTIMADO`); lint de mensajes bot a bot, briefs y resultados con topes de tokens por tipo y detección de contenido pegado. |

### Invariantes nuevas

- **I-LEDGER** `parse_report` nunca inventa filas: cada `ShareRow` sale de una línea `- nombre: N%` del texto; si el texto no trae ninguna línea de share ni `Weekly usage limit`, falla con `DomainError`. `share_pct ∈ [0, 100]`. Las filas hijas cuelgan de la última fila de nivel superior. Las shares de un mismo nivel no se normalizan ni se corrigen.
- **I-BURN** La quema se calcula solo entre lecturas consecutivas de la **misma** ventana (`reset_at` igual) con `Δt > 0`; nunca cruza un reset. Una lectura menor dentro de la ventana no produce quema negativa (se ignora el par).
- **I-LEAK** Cada `Finding` declara `label ∈ {MEDIDO, ESTIMADO}`: es `MEDIDO` solo si todos sus números salen de filas o lecturas registradas; cualquier proyección o atribución es `ESTIMADO`. `leak-scan` sale con código 7 si hay algún `crit`.
- **I-LINT** `msg-lint` usa la misma estimación de tokens que el resto (`ceil(len/4)`); topes por tipo `handoff 280`, `brief 700`, `result 350`, `user 250`. Un bloque de código o log pegado de más de 20 líneas es violación aunque el total quepa.

### Casos de uso y CLI

- `usage-ingest --file REPORTE.txt [--reset-at ISO]`: guarda el reporte parseado en `$C9MEM_SHARES_LOG` (JSONL append-only con lock). Si trae `Weekly usage limit: N% used` y se pasa `--reset-at`, también registra la lectura en el semáforo F31.
- `usage-burn`: quema por par de lecturas y proyección de agotamiento de la ventana activa.
- `leak-scan [--now ISO]`: aplica las reglas al último reporte de cada desglose y a la quema; imprime hallazgos ordenados por severidad.
- `msg-lint --kind {handoff,brief,result,user} [--file F | stdin]`: veredicto, tokens, tope y problemas; código 8 si no pasa.
