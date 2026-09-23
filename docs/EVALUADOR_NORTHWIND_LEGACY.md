# Evaluador anterior (Northwind), archivado

**Ubicación:** `legacy/evaluator_northwind/` (desde la Fase 7). Contiene
`src/evaluator/` (`banco.py`, `comparar.py`, `evaluador.py`) y su test,
`tests/test_evaluator.py`.

## Qué era

El mecanismo que usaba el proyecto **antes** de migrar a Instacart para
medir la precisión del LLM local:

- `banco.py`: un banco fijo de preguntas **sobre Northwind Traders**, cada
  una con su SQL de referencia escrito a mano.
- `evaluador.py`: corría cada pregunta por el chat de Northwind
  (`src.llm.responder`, también archivado en `legacy/northwind/`), ejecutaba
  el SQL de referencia contra `data/northwind.db` y comparaba los dos
  resultados.
- `comparar.py`: la comparación tabular de esos resultados.

Sus reportes (`docs/evaluaciones/*.md` y `docs/reporte_evaluacion.md`)
salieron de correr ese banco sobre Northwind. Se movieron a `legacy/docs/`:
quedaron obsoletos al cambiar el dataset.

## Qué NO es

**No es** el mecanismo de comparación "IA contra respuestas de referencia"
que pide el brief de Instacart. Ese sigue **pendiente para una etapa
futura** (el brief lo deja fuera de alcance y ninguna fase lo implementó).
Cuando se construya, las diferencias serán de fondo:

| | Evaluador archivado (Northwind) | Comparación futura (Instacart) |
|---|---|---|
| Dataset | Northwind (SQLite) | Instacart (DuckDB, más de 33M líneas) |
| "Verdad" | SQL de referencia escrito a mano por pregunta | Las respuestas deterministas de `src/analytics` (N1-N11), las mismas que muestran el dashboard y el PDF |
| Chat evaluado | `src.llm.responder` (retirado) | `src.llm.responder_instacart` |

## Estado

Queda **como referencia histórica, no para ejecutarse**. Sus imports
apuntan a rutas que ya no existen (`src.evaluator`, `src.llm.responder`,
`src.db.database`), y `pytest` no lo recolecta (`testpaths = ["tests"]`).
El historial completo está en git: los archivos se movieron con `git mv`,
así que `git log --follow <ruta>` muestra su historia.
