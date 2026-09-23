# Fase 7: cierre (retiro de Northwind y del simulador, evaluador archivado, README final)

Rama: `migracion-instacart`. Fecha: 2026-09-23. Última fase de la
migración.

Qué hizo esta fase:
- movió el código de Northwind, el simulador y el evaluador anterior a
  `legacy/` con `git mv`, así que el historial se conserva
  (`git log --follow`);
- quitó dependencias y variables de entorno sin consumidores;
- reescribió el README.

**No se implementó nada nuevo, y el comportamiento de la ruta Instacart no
cambió.**

---

## 0. Paso 0

El commit de la Fase 6 existe y el árbol estaba limpio: no hizo falta
comitear nada.

```
commit a92c9dbf817784d11e5aa6f86461d567e680a3fa
    Fase 6: interfaz de dos paneles, chat Instacart con historial
 10 files changed, 1734 insertions(+), 173 deletions(-)
```

---

## 1. Hallazgo previo: código activo importaba Northwind

El grep (no una suposición) mostró que **la ruta Instacart dependía de
módulos que esta fase tenía que retirar**. En la Fase 5 se había decidido
reusar y no duplicar:

| Archivo activo | Importaba | De |
|---|---|---|
| `src/llm/safety_instacart.py` | `PALABRAS_PROHIBIDAS`, `SqlNoSeguro`, `_sin_comentarios`, `validar_sql` | `src.llm.safety` |
| `src/llm/nl2sql_instacart.py` | `limpiar_sql` | `src.llm.nl2sql` |
| `scripts/benchmark_modelos_llm.py` | `limpiar_sql` (import local) | `src.llm.nl2sql` |
| `scripts/prueba_concurrencia.py` | `responder_pregunta`, `crear_base_de_datos`, `modelo_actual` | `src.llm.responder`, `src.db.database`, `src.llm.client` |
| `tests/conftest.py` | fixture `autouse` que recreaba el esquema SQLite **antes de cada test del proyecto** | `src.db.schema`, `src.db.database` |
| `tests/test_safety.py` | `validar_sql`, `es_seguro`, `limpiar_sql` (funciones que la ruta Instacart **sigue usando**) | `src.llm.safety`, `src.llm.nl2sql` |

Mover los archivos de la lista sin resolver esto primero habría roto el
chat de Instacart. El punto 4 del prompt lo pide ("si algo lo hace, es un
error de esta fase, corrígelo"), así que el **bloque 0** se hizo antes de
mover nada.

---

## 2. Movimientos por bloque, con `pytest -q` después de cada uno

Línea base antes de mover: **349 tests** (347 passed + 2 xfailed).

### Bloque 0: desacoplar el código activo (sin mover nada)
- `validar_sql`, `es_seguro`, `_sin_comentarios`, `SqlNoSeguro` y
  `PALABRAS_PROHIBIDAS` se **trasladaron sin cambios** a
  `src/llm/safety_instacart.py`, y `limpiar_sql` (con `_FENCE`) a
  `src/llm/nl2sql_instacart.py`. La igualdad se verificó con
  `inspect.getsource(original) == inspect.getsource(trasladada)` → `True`
  para las 4 funciones, y también las constantes son iguales.
- `tests/test_safety.py`: solo cambian sus 2 líneas de import, que ahora
  apuntan a los módulos Instacart. Sigue activo porque prueba código vivo.
- `scripts/benchmark_modelos_llm.py`: el import de `limpiar_sql` apunta a
  `nl2sql_instacart`.
- `scripts/prueba_concurrencia.py`: la carga de Ollama pasa de
  `responder_pregunta` (Northwind) a `responder_pregunta_instacart`, y se
  quitó `crear_base_de_datos()`. Queda documentado en su docstring que una
  corrida nueva no es comparable 1:1 con la de la Fase 3.

`pytest -q` → **347 passed, 2 xfailed** (sin cambios).

### Bloque 1: evaluador → `legacy/evaluator_northwind/`
| Origen | Destino |
|---|---|
| `src/evaluator/__init__.py`, `banco.py`, `comparar.py`, `evaluador.py` | `legacy/evaluator_northwind/src/evaluator/` |
| `tests/test_evaluator.py` (18 tests) | `legacy/evaluator_northwind/tests/test_evaluator.py` |

`pytest -q` → **329 passed, 2 xfailed** (349 − 18 = 331 ✓).

### Bloque 2: chat de Northwind → `legacy/northwind/`
| Origen | Destino |
|---|---|
| `src/llm/prompts.py`, `nl2sql.py`, `safety.py`, `responder.py` | `legacy/northwind/src/llm/` |
| `src/ui/app.py` | `legacy/northwind/src/ui/app.py` |
| `tests/test_responder.py` (2 tests) | `legacy/northwind/tests/` |

`pytest -q` → **327 passed, 2 xfailed** (331 − 2 = 329 ✓).

### Bloque 3: simulador y SQLite → `legacy/northwind/`
Antes de mover, el grep confirmó que los únicos consumidores de
`src.db.database` y `src.db.schema` eran los archivos de este mismo bloque.

| Origen | Destino |
|---|---|
| `src/generator/` completo (`__init__`, `catalog`, `catalogo_negocio`, `generator`, `seed`) | `legacy/northwind/src/generator/` |
| `src/db/schema.py` | `legacy/northwind/src/db/schema.py` |
| `src/db/database.py` | `legacy/northwind/src/db/database.py` (ver sección 4) |
| `tests/test_generator.py` (12), `test_catalog.py` (4), `test_catalogo_negocio.py` (5), `test_schema.py` (5) | `legacy/northwind/tests/` |
| `tests/conftest.py` (solo contenía el fixture de SQLite de Northwind) | `legacy/northwind/tests/conftest.py` |

`pytest -q` → **301 passed, 2 xfailed** (329 − 26 = 303 ✓).

### Bloque 4: reportes del banco viejo y base del simulador
| Origen | Destino |
|---|---|
| `docs/evaluaciones/` (`comparativa.md`, `llama3.1-8b.md`, `qwen2.5-coder-14b.md`, `qwen2.5-coder-1.5b.md`) | `legacy/docs/evaluaciones/` |
| `docs/reporte_evaluacion.md` | `legacy/docs/reporte_evaluacion.md` |
| `data/ventia.db` (1.4 MB; **no estaba versionado**, lo ignoraba `data/*.db`) | **borrado del disco** |

`pytest -q` → **301 passed, 2 xfailed**.

### Bloque 5: variables de entorno y dependencias
(Sección 5.) `pytest -q` → **301 passed, 2 xfailed**.

### Archivos nuevos
- `legacy/README.md`: índice de lo archivado, con la ruta original de cada
  archivo.
- `docs/EVALUADOR_NORTHWIND_LEGACY.md`: nota que explica que el evaluador
  archivado es el mecanismo de comparación **anterior**, distinto de la
  comparación IA contra referencia de Instacart, que sigue pendiente.

---

## 3. Número final de tests y desglose

| | Tests |
|---|---:|
| Antes de la Fase 7 | 349 (347 passed + 2 xfailed) |
| − `test_evaluator.py` | −18 |
| − `test_responder.py` | −2 |
| − `test_generator.py` | −12 |
| − `test_schema.py` | −5 |
| − `test_catalogo_negocio.py` | −5 |
| − `test_catalog.py` | −4 |
| **Retirados** | **−46** |
| **Quedan** | **303 (301 passed + 2 xfailed)** |

`tests/test_safety.py` (14 tests) **no se retiró**: prueba `validar_sql`,
`es_seguro` y `limpiar_sql`, que siguen en uso en la ruta Instacart. Solo
cambió su import (sección 2, bloque 0). Los 2 xfailed son las mismas
limitaciones medidas de qwen2.5-coder:1.5b de la Fase 5.

---

## 4. `src/db/database.py`: se retiró completo, no se editó

El prompt pedía quitar la lógica especial de `northwind.db` y, si el
módulo quedaba sin uso real, documentar si se retiraba. Resultado del grep
**después** de los bloques 0-2: sus únicos consumidores eran `schema.py`,
`src/generator/*`, `tests/conftest.py`, `test_schema.py` y
`test_generator.py`, todos retirados en el bloque 3. La ruta Instacart no lo
usa (`src/db/instacart.py` y `instacart_validacion.py` solo importan
`duckdb`). Como era un engine de SQLAlchemy exclusivo de
Northwind/simulador, **se movió completo a `legacy/`**. La lógica especial
de `northwind.db` se va con él. `src/db/` queda con `__init__.py`,
`instacart.py` e `instacart_validacion.py`.

---

## 5. Variables de entorno y `requirements.txt`

**Variables: se eliminaron del código, no se dejaron sin efecto.** Una
variable que no hace nada es una trampa para quien la configure.

| Variable | Consumidor que tenía | Decisión |
|---|---|---|
| `VENTIA_DB_PATH` | `src/db/database.py` (y `tests/conftest.py`) | Desaparece con `database.py` y `conftest.py` → `legacy/`. |
| `VENTIA_LLM_MODELO` | `client.modelo_actual()` / `client.chat()`, usadas por el chat de Northwind, `src/ui/app.py`, el evaluador y `prueba_concurrencia.py` | `modelo_actual()`, `chat()` y `MODELO_POR_DEFECTO` **se borraron de `src/llm/client.py`**. Después del bloque 2 el grep solo las encontraba en `client.py` (`prueba_concurrencia.py` usa `modelo_actual_instacart` con un alias). Se actualizaron los docstrings de `client.py`. |
| `VENTIA_LLM_MODELO_INSTACART` | `client.modelo_actual_instacart()` | **Vigente.** |

API final de `src/llm/client.py`: `LlmNoDisponible`, `LlmTimeout`,
`MODELO_POR_DEFECTO_INSTACART`, `modelo_actual_instacart`,
`modelos_disponibles`, `chat_con_modelo` y `chat_con_modelo_stream`.

**`requirements.txt`:**
- **Se quitó `faker`**: lo usaban solo `src/generator/generator.py` y
  `catalogo_negocio.py`. El grep sobre `src scripts tests` ya no encuentra
  `faker` ni `Faker`.
- **Se quitó `sqlalchemy`**: lo usaban solo `database.py`, `schema.py`,
  `generator`, el evaluador, `responder.py` y `app.py`, todos retirados. El
  grep ya no lo encuentra.
- Las demás tienen consumidor vivo, verificado con grep:
  - `streamlit`: UI y caché;
  - `ollama`: `client`;
  - `pandas` y `altair`: dashboard;
  - `duckdb`: todo el análisis;
  - `reportlab` y `matplotlib`: el PDF;
  - `pypdf`: tests del PDF.
- `requirements-dev.txt` no cambia (`-r requirements.txt` + `pytest`).
- También se actualizó la `description` de `pyproject.toml`, que todavía
  describía el simulador.

**`data/northwind.db`**: el README nuevo ya no lo menciona (el viejo pedía
"colocarlo a mano"). El archivo **sigue en disco** (24.7 MB, no versionado,
ignorado por `data/*.db`) porque el prompt solo mandaba borrar
`data/ventia.db`. Ya nadie lo lee; se puede borrar a mano.

`.gitignore` no cambió: `data/*.db` sigue siendo útil mientras exista
`northwind.db` en disco, y los directorios de Instacart siguen igual.

---

## 6. Nada activo importa de `legacy/`

- **Grep** de imports de los módulos retirados o de `legacy` en `src/`,
  `scripts/` y `tests/`: **ninguno**. Las únicas menciones de "legacy" en
  código activo son 3 docstrings (`client.py`, `nl2sql_instacart.py`,
  `safety_instacart.py`) que explican el traslado.
- **Importación real** de los 40 módulos activos de `src/` y `scripts/`
  (todos menos los dos scripts de Streamlit, que se prueban aparte) en un
  proceso limpio: **40 OK, 0 fallos**. Después de importarlos,
  `sys.modules` no contiene ningún módulo retirado.
- `legacy/` no tiene `__init__.py` y `pytest` no lo recolecta
  (`testpaths = ["tests"]`).

---

## 7. La app arranca

`streamlit run src/ui/app_instacart.py --server.headless true --server.port 8598`
con la base y Ollama reales. `/_stcore/health` responde `ok`, y el servidor
no registró ningún traceback (solo el banner de URLs). Se recorrió con el
mismo cliente websocket de la Fase 6:

```
 0.02s  NEW_SESSION
 1.57s  TITULO dashboard: N1 — Recompra por producto
 1.74s  SCRIPT_FINISHED FINISHED_SUCCESSFULLY                  ← carga inicial sin errores de import
 1.74s  >>> ENVÍO pregunta al chat: '¿Cuántos pedidos hay en total?'
 2.68s  STATUS chat: Consulta lista
 2.74s  >>> TOCO el dashboard (radio -> N3), rerun de FRAGMENTO
 5.94s  CHAT texto: '**Respuesta corregida:** Hay 3,421,083 pedidos en total.'
 5.94s  SCRIPT_FINISHED FINISHED_SUCCESSFULLY
 5.97s  TITULO dashboard: N3 — Picos de demanda
 6.08s  SCRIPT_FINISHED FINISHED_FRAGMENT_RUN_SUCCESSFULLY
```

El comportamiento es idéntico al de la Fase 6: chat con validación de
cifras y dashboard en un fragmento que espera al chat.

---

## 8. Desviaciones (con motivo)

1. **Se tocaron `safety_instacart.py` y `nl2sql_instacart.py`**, que el
   prompt pedía dejar "tal cual salvo lo que pida el punto 4". Es justo lo
   que pide el punto 4: importaban módulos que esta fase retira. Se
   trasladó el código **sin cambios** (verificado con `inspect.getsource`)
   y el comportamiento es idéntico (303 tests iguales).
2. **`tests/test_safety.py` se quedó activo** (con 2 líneas de import
   cambiadas) en vez de retirarse como "test de Northwind". Prueba
   funciones que siguen en uso; retirarlo habría dejado sin prueba directa
   a `validar_sql`/`limpiar_sql`.
3. **Se modificó `scripts/prueba_concurrencia.py`** (no estaba en la
   lista). Importaba el chat de Northwind y `benchmark_modelos_llm.py`
   importa su monitor de memoria. Archivarlo habría roto el benchmark, y
   dejarlo igual habría dejado un import roto. Se cambió la carga de Ollama
   a la ruta Instacart y se documentó que una corrida nueva no es
   comparable con la de la Fase 3.
4. **`tests/conftest.py` se archivó** (tampoco estaba en la lista). Todo su
   contenido era el fixture `autouse` de SQLite de Northwind, que dependía
   de `schema.py`.
5. **`src/db/database.py` se archivó completo** en vez de solo quitarle el
   caso especial (sección 4).
6. **Se editaron `src/llm/client.py` y `pyproject.toml`**: se borró la API
   de Northwind sin consumidores (sección 5) y se actualizó la
   descripción. No cambia nada que use Instacart.
7. **`data/northwind.db` sigue en disco** (sección 5): borrarlo no estaba
   en la lista.
8. **`docs/arquitectura.md` no se movió** aunque describe la arquitectura
   de Northwind: no estaba en la lista. El README nuevo no lo enlaza.
   Conviene archivarlo o reescribirlo en otra iteración (ver riesgos).

---

## 9. Riesgos

- **`docs/arquitectura.md` y `docs/AUDITORIA.md` describen el sistema
  anterior.** `AUDITORIA.md` es histórica a propósito. `arquitectura.md`
  puede confundir: está desactualizado y no se enlaza desde el README.
- **Un checkout que todavía tenga `.venv` con Faker/SQLAlchemy no se
  rompe**, pero una instalación nueva ya no los trae: correr algo de
  `legacy/` fallaría por imports. Es intencional (no se ejecuta).
- **Los reportes de la Fase 1 a la 6 mencionan rutas que ya no existen**
  (`src/llm/responder.py`, `src/ui/app.py`, `tests/test_evaluator.py`…).
  Son registros históricos y no se reescribieron. `legacy/README.md` da la
  ruta nueva de cada archivo.
- **`prueba_concurrencia.py` ahora mide con otra ruta de chat.** Sus
  resultados nuevos no se pueden poner lado a lado con los de la Fase 3.
- Siguen vigentes todas las limitaciones de la IA y de la interfaz,
  consolidadas en el README ("Qué garantiza la IA y qué no" y
  "Limitaciones conocidas").

---

## 10. Estado final

`git status --short` antes del commit (los `R` son `git mv`):
```
M  README.md
A  docs/EVALUADOR_NORTHWIND_LEGACY.md
A  docs/FASE7_REPORTE.md
A  legacy/README.md
R  docs/evaluaciones/comparativa.md -> legacy/docs/evaluaciones/comparativa.md
R  docs/evaluaciones/llama3.1-8b.md -> legacy/docs/evaluaciones/llama3.1-8b.md
R  docs/evaluaciones/qwen2.5-coder-1.5b.md -> legacy/docs/evaluaciones/qwen2.5-coder-1.5b.md
R  docs/evaluaciones/qwen2.5-coder-14b.md -> legacy/docs/evaluaciones/qwen2.5-coder-14b.md
R  docs/reporte_evaluacion.md -> legacy/docs/reporte_evaluacion.md
R  src/evaluator/__init__.py -> legacy/evaluator_northwind/src/evaluator/__init__.py
R  src/evaluator/banco.py -> legacy/evaluator_northwind/src/evaluator/banco.py
R  src/evaluator/comparar.py -> legacy/evaluator_northwind/src/evaluator/comparar.py
R  src/evaluator/evaluador.py -> legacy/evaluator_northwind/src/evaluator/evaluador.py
R  tests/test_evaluator.py -> legacy/evaluator_northwind/tests/test_evaluator.py
R  src/db/database.py -> legacy/northwind/src/db/database.py
R  src/db/schema.py -> legacy/northwind/src/db/schema.py
R  src/generator/__init__.py -> legacy/northwind/src/generator/__init__.py
R  src/generator/catalog.py -> legacy/northwind/src/generator/catalog.py
R  src/generator/catalogo_negocio.py -> legacy/northwind/src/generator/catalogo_negocio.py
R  src/generator/generator.py -> legacy/northwind/src/generator/generator.py
R  src/generator/seed.py -> legacy/northwind/src/generator/seed.py
R  src/llm/nl2sql.py -> legacy/northwind/src/llm/nl2sql.py
R  src/llm/prompts.py -> legacy/northwind/src/llm/prompts.py
R  src/llm/responder.py -> legacy/northwind/src/llm/responder.py
R  src/llm/safety.py -> legacy/northwind/src/llm/safety.py
R  src/ui/app.py -> legacy/northwind/src/ui/app.py
R  tests/conftest.py -> legacy/northwind/tests/conftest.py
R  tests/test_catalog.py -> legacy/northwind/tests/test_catalog.py
R  tests/test_catalogo_negocio.py -> legacy/northwind/tests/test_catalogo_negocio.py
R  tests/test_generator.py -> legacy/northwind/tests/test_generator.py
R  tests/test_responder.py -> legacy/northwind/tests/test_responder.py
R  tests/test_schema.py -> legacy/northwind/tests/test_schema.py
M  pyproject.toml
M  requirements.txt
M  scripts/benchmark_modelos_llm.py
M  scripts/prueba_concurrencia.py
M  src/llm/client.py
M  src/llm/nl2sql_instacart.py
M  src/llm/safety_instacart.py
M  tests/test_safety.py
```

`pytest -q` final:
```
301 passed, 2 xfailed in 185.27s
```

`pytest -m integration -q`:
```
34 passed, 267 deselected, 2 xfailed in 103.96s
```

---

## 11. README nuevo (completo)

El texto de abajo es el contenido exacto de `README.md` en este commit.

````markdown
# ventia: Instacart con IA local

Web app de dos paneles sobre **The Instacart Online Grocery Shopping
Dataset 2017** (pedidos reales de supermercado en línea):

- **Izquierda: chat con una IA local** (Ollama). Traduce la pregunta en
  español a SQL de solo lectura, la ejecuta contra los datos reales y
  redacta la respuesta en streaming. El SQL queda visible en cada turno y
  el historial se conserva durante la sesión.
- **Derecha: dashboard de 11 necesidades analíticas** (N1-N11: recompra,
  canasta, picos de demanda, frecuencia, productos ancla, departamentos,
  evolución, rutinarios/exploradores, tamaño de pedido, impulso y
  predicción del siguiente pedido), con gráficas, valores clave y la
  decisión que ayuda a tomar. Incluye un botón para (re)generar un **PDF
  determinista, sin IA**: cada cifra sale de una consulta SQL.

**Ficha del dataset** (fuente, licencia, limitaciones, filas y columnas por
tabla, medidas con consultas reales): [`docs/DATASET_INSTACART.md`](docs/DATASET_INSTACART.md).
Este README no repite sus cifras: esa ficha es la fuente autorizada.

El encargo completo está en [`docs/BRIEF_INSTACART.md`](docs/BRIEF_INSTACART.md)
y las definiciones formales de N1-N11 en [`docs/NECESIDADES.md`](docs/NECESIDADES.md).

---

## Instalación

Requisitos: Python 3.11 o superior (probado con 3.12), y
[Ollama](https://ollama.com) corriendo en local solo para el chat.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # incluye pytest; requirements.txt solo lo de ejecución
ollama pull qwen2.5-coder:1.5b                    # modelo por defecto del chat
```

(En Windows: `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`.)

## Cargar los datos

1. Descarga los 6 CSV de Instacart (copia usada: Kaggle, usuario *yasserh*;
   licencia original de uso no comercial) y colócalos en
   `data/instacart/raw/`: `aisles.csv`, `departments.csv`, `products.csv`,
   `orders.csv`, `order_products__prior.csv` y `order_products__train.csv`.
2. Construye la base DuckDB, con esquema explícito y construcción atómica:
   ```bash
   .venv/bin/python -m src.db.instacart --raw data/instacart/raw --out data/instacart.duckdb
   ```
   Opciones: `--rebuild` (reconstruir si ya existe), `--memory-limit 4GB`
   (valor por defecto) y `--threads N`.
3. (Opcional) Valida la carga contra los CSV:
   ```bash
   .venv/bin/python -m src.db.instacart_validacion --db data/instacart.duckdb --raw data/instacart/raw
   ```
4. Genera las tablas agregadas que usan las necesidades pesadas:
   ```bash
   .venv/bin/python -m src.analytics.agregados --db data/instacart.duckdb --out data/instacart_agg.duckdb
   ```
5. (Opcional) Precalcula las 11 respuestas de referencia en `data/referencia/`.
   Si no lo haces, el dashboard las calcula la primera vez y las guarda en
   caché; también puedes usar su botón "Actualizar datos".
   ```bash
   .venv/bin/python -m src.analytics --todas --db data/instacart.duckdb --agg data/instacart_agg.duckdb --out-dir data/referencia
   ```

Todo lo que se genera (`data/*.duckdb`, `data/referencia/`, `data/cache/`,
`data/pdf/`) está en `.gitignore`.

## Correr la app de dos paneles

```bash
.venv/bin/streamlit run src/ui/app_instacart.py
```

- **Dashboard standalone** (sin chat): `.venv/bin/streamlit run src/ui/dashboard.py`
- **Chat por línea de comandos**: `.venv/bin/python -m src.llm.responder_instacart "¿Cuántos pedidos hay en total?"`
- **PDF por línea de comandos**: `.venv/bin/python -m src.pdf.generador` (añade `--forzar` para recalcular las 11 necesidades)

## Variables de entorno

| Variable | Default | Efecto |
|---|---|---|
| `VENTIA_LLM_MODELO_INSTACART` | `qwen2.5-coder:1.5b` | Modelo de Ollama del chat. Se lee en cada pregunta. El default se eligió con el benchmark de 3 modelos de la Fase 5. |
| `OLLAMA_HOST` | `http://localhost:11434` | La lee la librería `ollama`: dónde está el servidor. |

Las variables de la etapa Northwind (`VENTIA_DB_PATH`, `VENTIA_LLM_MODELO`)
**se eliminaron del código** en la Fase 7. Ya no tienen ningún efecto.

## Pruebas

```bash
.venv/bin/python -m pytest -q                  # suite completa
.venv/bin/python -m pytest -m integration -q   # solo las que usan data/instacart.duckdb real
```

- La mayoría de los tests usan fixtures chicas (`tests/fixtures/`) y
  sustituyen el modelo por un doble, así que no necesitan Ollama.
- Las pruebas `integration` se saltan si falta `data/instacart.duckdb`.
- Las de coherencia del chat (`tests/test_coherencia_instacart.py`) se
  saltan si Ollama no responde.

---

## Qué garantiza la IA y qué no

El chat usa un modelo local chico (qwen2.5-coder:1.5b). Las guardas
garantizan **coherencia estructural**: que la respuesta sea fiel a los datos
que devolvió la consulta. **No garantizan que la consulta haya sido la
correcta para la pregunta.**

**Sí garantiza (con código y tests, no solo con el prompt):**
- **Solo lectura, una sentencia, con LIMIT y timeout.** Hay dos barreras:
  la validación de texto (`validar_sql`) y una conexión DuckDB
  `read_only=True` a nivel de motor. El LIMIT (1000) se fuerza y la
  consulta tiene un timeout real de 10 s. En la Fase 5 se desactivó cada
  guarda a propósito y su test falló. Ejemplo real: *"Borra todos los
  pedidos"* → el modelo generó `DELETE FROM orders` → se rechazó sin
  reintento (Fase 5 y Fase 6, sección 6).
- **No inventa tablas ni columnas que se ejecuten.** El esquema del prompt
  se obtiene leyendo la base real. Un SQL con una columna inexistente falla
  en el motor, se reintenta una sola vez con el motivo exacto y, si vuelve
  a fallar, el chat muestra un error controlado, nunca un resultado
  inventado.
- **No inventa dinero.** El dataset no tiene precios. A *"¿Cuánto dinero
  se gastó?"* el modelo intentó `SUM(price)`, el motor lo rechazó dos
  veces y el chat terminó en un error controlado, sin ningún monto (Fase 5).
- **No altera las cifras del resultado.** Cada número de la respuesta se
  compara contra el resultado de la consulta. Caso real: la base devolvió
  3421083 y el modelo escribió *"Hay 34,210,83 pedidos"*. Ahora se detecta
  y la respuesta final es *"Hay 3,421,083 pedidos en total."* Si la
  segunda redacción también falla, se usa una plantilla con el valor real
  (Fase 6). Como la respuesta llega en streaming, la cifra alterada puede
  verse unos segundos antes de que aparezca el aviso y la versión corregida.

**NO garantiza:**
- **Corrección semántica.** Si el SQL responde otra pregunta, la cifra es
  fiel a *ese* resultado y pasa todas las guardas. Caso real (Fase 6,
  turno 1): a *"¿Cuántos pedidos hay de tipo train?"* el modelo contó las
  **líneas** de `order_products__train` en vez de los **pedidos** con
  `eval_set = 'train'`, y respondió con esa cifra de líneas como si fueran
  pedidos.
- **Acertar en consultas con JOIN.** En el banco de 15 preguntas de la
  Fase 5, qwen2.5-coder:1.5b produjo SQL válido en el 54% de las preguntas
  no trampa; el reintento no mejoró esa tasa. Dos preguntas del test de
  coherencia están marcadas `xfail` por esta limitación medida.
- **Resolver bien las preguntas de seguimiento.** Ver "Limitaciones conocidas".

**Pendiente para una etapa futura:** la **comparación automática** de las
respuestas del chat contra las respuestas de referencia deterministas de
`src/analytics` (las mismas del dashboard y el PDF). Ninguna fase la
implementó: el brief la dejó fuera de alcance. El evaluador de la etapa
Northwind es otro mecanismo y está archivado
(ver [`docs/EVALUADOR_NORTHWIND_LEGACY.md`](docs/EVALUADOR_NORTHWIND_LEGACY.md)).

---

## Limitaciones conocidas del sistema completo

Consolidadas de los reportes de fase; el detalle y las cifras están en cada
documento enlazado.

**Del dataset** (ver [`docs/DATASET_INSTACART.md`](docs/DATASET_INSTACART.md)):
- Sin precios, fechas reales ni datos demográficos: nada del sistema puede
  hablar de dinero ni de calendario.
- `order_dow` y `order_hour_of_day` son anónimos: se etiquetan "Día 0..6" y
  "Hora 0..23", nunca "lunes".
- Los pedidos `test` no tienen productos y se excluyen de todo análisis de
  productos.

**De las necesidades** (ver [`docs/NECESIDADES.md`](docs/NECESIDADES.md)):
- **N4**: `days_since_prior_order` está topado en 30. Una parte de los
  usuarios "en riesgo" tiene su último intervalo censurado (el real podría
  ser mayor). Además no existe "hoy": se compara el último intervalo contra
  el patrón propio del usuario, nunca "días sin comprar hasta hoy".
- **N11**: el baseline (frecuencia + recencia, sin ML) tiene un **techo**:
  solo puede acertar productos que ya estaban en el historial del usuario.
  El hit-rate se reporta junto con ese techo.
- **N5 y N10**: el orden de agregado al carrito puede reflejar la
  **interfaz** de Instacart (p. ej. listas de "comprar de nuevo" primero),
  no una preferencia. "Producto ancla" e "impulso" son hipótesis, no hechos.
- **N7 y N9** son descriptivas, no causales.
- N2 (lift) favorece pares raros; se exige un soporte mínimo documentado.

**Del chat** (ver [Fase 5](docs/FASE5_REPORTE.md) y [Fase 6](docs/FASE6_REPORTE.md)):
- Corrección semántica no garantizada (sección anterior).
- **Una pregunta de seguimiento hereda los errores del turno anterior.** Al
  modelo se le mandan los últimos 3 turnos exitosos como (pregunta, SQL).
  *"¿y de tipo test?"* se resuelve bien si el SQL anterior era correcto, y
  falla si no lo era (caso real de la Fase 6).
- La primera pregunta después de arrancar Ollama tarda más (carga del
  modelo). El timeout del modelo es de 30 s por llamada; en streaming es
  por fragmento.
- La validación de cifras puede descartar una cifra derivada legítima (un
  cálculo que no está en el resultado) y sustituirla por el reintento o la
  plantilla. Es un falso positivo seguro.

**De la interfaz** (ver [Fase 6](docs/FASE6_REPORTE.md), sección 5):
- **Los paneles no son concurrentes.** Streamlit ejecuta un solo script a
  la vez por sesión:
  - si tocas el dashboard mientras el chat genera, el cambio espera a que
    el chat termine;
  - si mandas una pregunta mientras se genera el PDF, el PDF se escribe
    pero se pierde su mensaje de confirmación.

**Del rendimiento** (ver [Fase 1](docs/FASE1_REPORTE.md) y [Fase 3](docs/FASE3_REPORTE.md)):
- Los paneles se sirven desde caché (`data/referencia/`, invalidada por la
  huella de la base). Recalcular N10 y N11 tarda varios segundos y solo
  ocurre con "Actualizar datos", nunca al cambiar de panel.

---

## Historial de fases

| Fase | Qué se hizo | Commit | Reporte |
|---|---|---|---|
| 0 | Auditoría del proyecto Northwind antes de migrar | — | [`AUDITORIA.md`](docs/AUDITORIA.md) |
| 1 | Capa de datos: carga de los 6 CSV a DuckDB con esquema explícito, validación y ficha del dataset | `2657cc4` | [`FASE1_REPORTE.md`](docs/FASE1_REPORTE.md) |
| 2A | Contrato analítico (`ResultadoNecesidad`) y necesidades N1-N6 | `e383f1e` | [`FASE2A_REPORTE.md`](docs/FASE2A_REPORTE.md) |
| 2B | Correcciones de la 2A y necesidades N7-N11 | `109eee9` | [`FASE2B_REPORTE.md`](docs/FASE2B_REPORTE.md) |
| 3 | Dashboard de 11 paneles con caché y prueba de concurrencia con Ollama | `6909ee9` | [`FASE3_REPORTE.md`](docs/FASE3_REPORTE.md) |
| 4 | PDF determinista sin IA, integrado al dashboard | `fb3a697` | [`FASE4_REPORTE.md`](docs/FASE4_REPORTE.md) |
| 5 | IA local sobre Instacart: esquema leído de la base, guardas reales, reintento, benchmark de 3 modelos y modelo por defecto `qwen2.5-coder:1.5b` | `f7ccb28` | [`FASE5_REPORTE.md`](docs/FASE5_REPORTE.md) |
| 6 | App de dos paneles, chat con streaming e historial, validación de cifras | `a92c9db` | [`FASE6_REPORTE.md`](docs/FASE6_REPORTE.md) |
| 7 | Retiro de Northwind y del simulador a `legacy/`, limpieza de dependencias y este README | ver `git log` | [`FASE7_REPORTE.md`](docs/FASE7_REPORTE.md) |

El código de la etapa anterior (chat sobre Northwind, simulador de ventas
sintéticas y evaluador del banco de Northwind) está archivado en
[`legacy/`](legacy/README.md), con su historial de git intacto.
````
