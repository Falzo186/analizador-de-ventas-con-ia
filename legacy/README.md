# legacy/: código retirado en la Fase 7

Aquí está el código de **antes** de migrar a Instacart. Se conserva como
referencia histórica, **no para ejecutarse**:
- sus imports apuntan a rutas `src.*` que ya no existen;
- `pytest` no recolecta estos tests (`testpaths = ["tests"]` en `pyproject.toml`);
- ningún archivo activo importa nada de aquí (verificado en `docs/FASE7_REPORTE.md`).

Todo se movió con `git mv`: `git log --follow <ruta>` muestra la historia
completa de cada archivo.

| Carpeta | Contenido | Ruta original |
|---|---|---|
| `northwind/src/llm/` | Chat sobre Northwind: `prompts.py`, `nl2sql.py`, `safety.py`, `responder.py` | `src/llm/` |
| `northwind/src/ui/app.py` | Interfaz Streamlit de Northwind | `src/ui/app.py` |
| `northwind/src/db/` | `schema.py` (SQLAlchemy del simulador) y `database.py` (engine SQLite, `VENTIA_DB_PATH`) | `src/db/` |
| `northwind/src/generator/` | Simulador de ventas sintéticas (Faker) | `src/generator/` |
| `northwind/tests/` | Sus tests (`test_responder`, `test_schema`, `test_generator`, `test_catalog`, `test_catalogo_negocio`) y el `conftest.py` que recreaba la BD SQLite antes de cada test | `tests/` |
| `evaluator_northwind/` | Evaluador del banco de preguntas de Northwind y su test. Ver `docs/EVALUADOR_NORTHWIND_LEGACY.md` | `src/evaluator/`, `tests/test_evaluator.py` |
| `docs/` | Reportes generados con ese evaluador (`evaluaciones/*.md`, `reporte_evaluacion.md`) | `docs/` |

Las funciones de texto que la ruta Instacart reutilizaba (`validar_sql`,
`es_seguro`, `limpiar_sql`) **se trasladaron sin cambios** a
`src/llm/safety_instacart.py` y `src/llm/nl2sql_instacart.py`. Las copias
de aquí son el original histórico.
