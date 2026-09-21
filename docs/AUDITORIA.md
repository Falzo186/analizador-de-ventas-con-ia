# Auditoría previa a la migración Northwind → Instacart

Fecha: 2026-09-21. Rama: `master`. Alcance: **solo lectura**, ningún archivo de
código fue modificado. Todo lo afirmado aquí se verificó ejecutando un comando
o leyendo el archivo citado; donde no pude verificar algo lo digo explícitamente.

---

## 1. Stack y ejecución

- **Lenguaje:** Python 3.12.14 (`.venv/bin/python --version`). `pyproject.toml:5` declara `requires-python = ">=3.11"`.
- **Frameworks/librerías** (`requirements.txt:1-15`, versiones fijadas con `==`, confirmadas instaladas con `.venv/bin/pip list`):
  - `streamlit==1.39.0` — UI web.
  - `ollama==0.3.3` — cliente del LLM local (instalado; binario `ollama` en `/usr/local/bin/ollama`, versión de servidor `0.34.0`, servicio systemd **activo**, modelos descargados: `qwen2.5-coder:1.5b`, `qwen2.5-coder:14b`, y según README también `llama3.1:8b`).
  - `pandas==2.2.3`, `sqlalchemy==2.0.35`, `faker==30.1.0`.
  - Dev: `pytest==8.3.3` (`requirements-dev.txt:1-5`).
  - `pyarrow` 25.0.1 está instalado pero no aparece en `requirements.txt` (dependencia transitiva de pandas/streamlit, no declarada explícitamente).
  - **DuckDB no está instalado** (`ModuleNotFoundError` al importar en `.venv` y en el intérprete de sistema).
- **Variables de entorno:**
  - `VENTIA_DB_PATH` (`src/db/database.py:25`): ruta a la base SQLite, default `data/northwind.db`.
  - `VENTIA_LLM_MODELO` (`src/llm/client.py:28`): modelo de Ollama, default `llama3.1:8b` (`src/llm/client.py:17`).
- **Comandos exactos de arranque** (README.md:53-105):
  ```
  python3 -m venv .venv
  .venv/bin/pip install -r requirements-dev.txt
  .venv/bin/python -m src.llm.responder "<pregunta>"          # CLI
  .venv/bin/streamlit run src/ui/app.py                        # UI web
  .venv/bin/python -m src.evaluator.evaluador --modelo <m> --salida docs/evaluaciones/<m>.md
  .venv/bin/python -m pytest -q                                 # tests
  ```
  Requiere `ollama serve` corriendo y el modelo descargado (`ollama pull ...`).

## 2. Árbol de carpetas y responsabilidad

```
.
├── data/
│   ├── northwind.db         # BD SQLite activa (24 MB), no versionada (.gitignore:4)
│   ├── ventia.db            # BD del simulador viejo (1.4 MB), no versionada
│   └── instacart/raw/       # CSVs de Instacart YA DESCARGADOS, sin trackear (untracked)
├── docs/
│   ├── arquitectura.md      # decisiones de diseño, historial de la migración a Northwind
│   ├── reporte_evaluacion.md
│   └── evaluaciones/        # reportes por modelo evaluado
├── src/
│   ├── db/                  # database.py (engine SQLAlchemy+SQLite), schema.py (ORM del simulador)
│   ├── llm/                 # client.py (Ollama), prompts.py (esquema Northwind hardcodeado), nl2sql.py, safety.py, responder.py
│   ├── evaluator/           # banco.py (10 preguntas+SQL de referencia), comparar.py, evaluador.py
│   ├── generator/           # simulador de punto de venta (Faker) — respaldo, sin uso en el flujo principal
│   └── ui/app.py            # única vista: Streamlit, dos pestañas (IA, explorar tablas)
├── tests/                   # pytest, 60 casos, corren contra un sqlite temporal (no contra northwind.db)
├── requirements.txt / requirements-dev.txt / pyproject.toml
└── .venv/
```

No existe ningún directorio de frontend separado (no hay `package.json`, no hay JS/TS/React en el repo) — toda la "web app" es una única app Streamlit (`src/ui/app.py`, 200 líneas).

## 3. Base de datos actual

- **Motor:** SQLite, vía SQLAlchemy `create_engine(f"sqlite:///{RUTA_DB}", future=True)` (`src/db/database.py:29`). Sin pool ni configuración especial (WAL, `PRAGMA`, etc. — no aparecen en el código).
- **Dónde vive:** `data/northwind.db`, un solo archivo, **no versionado** en git (`.gitignore:4` excluye `data/*.db`; README.md:22-38 explica que hay que colocarlo a mano).
- **Cómo se crea/carga:** `crear_base_de_datos()` (`src/db/database.py:36-58`) **no** crea el esquema de Northwind — solo verifica que el archivo exista y lanza `FileNotFoundError` si no (líneas 46-52). El archivo Northwind se obtiene externamente (README no dice de dónde exactamente, dice "cualquier port de Northwind a SQLite"). Si `VENTIA_DB_PATH` apunta a otro nombre de archivo, sí corre `Base.metadata.create_all()` con el esquema del simulador (`src/db/schema.py`).
- **Tamaño:** `data/northwind.db` = 24 MB (`du -h`).
- **Esquema (verificado con `sqlite3 data/northwind.db ".tables"` y `COUNT(*)`):** 14 tablas de negocio + vistas (`Categories`, `Products`, `Suppliers`, `Customers`, `Employees`, `Shippers`, `Orders`, `"Order Details"`, más `Regions`, `Territories`, `EmployeeTerritories`, `CustomerDemographics`, `CustomerCustomerDemo`, `sqlite_sequence`, y ~14 vistas). Filas reales: `Categories`=8, `Products`=77, `Suppliers`=29, `Customers`=93, `Employees`=9, `Shippers`=3, `Orders`=16,282, `"Order Details"`=609,283.
- **¿Aguanta analítica sobre 33M+ filas?** No lo pude probar directamente (no hay datos Instacart cargados en ninguna base todavía, y DuckDB no está instalado para comparar). Con evidencia indirecta:
  - El total real de Instacart es **33,819,106 filas de producto** (`order_products__prior.csv` = 32,434,489 líneas de datos + `order_products__train.csv` = 1,384,617, tras descontar cabeceras; verificado con `wc -l`), más `orders.csv` con 3,421,083 pedidos (75,000 de ellos `eval_set='test'`, verificado con `cut -d, -f3 orders.csv | sort | uniq -c`).
  - SQLite es de un solo escritor y motor de ejecución de un solo hilo por consulta; no tiene paralelismo intra-consulta ni columnar storage. Agregaciones tipo `GROUP BY`/`JOIN` sobre 33M filas en modo fila-por-fila típicamente escanean el archivo completo por consulta salvo que existan índices que cubran exactamente el filtro, y con GROUP BY sobre casi toda la tabla (como N1, N2, N3, N6, N9, N10) el índice no evita el escaneo del agregado.
  - El código actual (`src/ui/app.py:191-197`, `src/llm/responder.py:49-60`) ejecuta cada consulta síncronamente en el hilo de la request, sin caché ni tablas precalculadas — funciona bien a la escala actual de Northwind (609K filas) pero el brief exige <5s por panel sobre 33M+ filas y con 11 necesidades simultáneas en el dashboard; no hay en el repo ninguna infraestructura de agregados precalculados, particionado o columnar hoy.
  - Conclusión: **no puedo afirmar con evidencia ejecutada que SQLite aguante o no** el volumen Instacart dentro de <5s — no hay CSV cargado ni consulta corrida contra 33M filas en esta auditoría (regla de solo lectura/no instalar paquetes me impidió cargar los CSVs con `sqlite3 .import` o similar para medir). Ver sección 9 para la recomendación razonada sin ese benchmark.

## 4. Acoplamiento con Northwind (archivos y líneas)

| Archivo | Líneas | Qué depende de Northwind |
|---|---|---|
| `src/db/database.py` | 19-25, 39, 43, 46, 49 | Ruta default `data/northwind.db`; lógica especial que **omite** `create_all()` si el nombre de archivo es `northwind.db`. |
| `src/llm/prompts.py` | 19-27 (`PRIMERA_FECHA_ORDEN`/`ULTIMA_FECHA_ORDEN` hardcodeadas a 2012-07-10/2023-10-28), 85-250 (`_PLANTILLA_SQL`: nombre de las 8 tablas de negocio, columnas exactas, ejemplos de filas reales, fórmula de ingreso con `"Order Details"`, diccionario de términos), 263-274 (`_SISTEMA_RESPUESTA` menciona "Northwind Traders") | El **prompt completo del LLM (llamada 1)** es el esquema de Northwind escrito a mano; no hay introspección dinámica del esquema. |
| `src/llm/nl2sql.py` | usa `prompt_sql` de `prompts.py` (sin referencias directas a tablas, pero depende transitivamente) | — |
| `src/evaluator/banco.py` | 1-165 completo: los 10 `Caso` usan `Products`, `"Order Details"`, `Employees`, `Suppliers`, `Categories`, `Customers`, `Shippers` en el SQL de referencia; comentarios en 11-22 documentan cifras reales de Northwind (609,283 líneas, 93 clientes, etc.) | El banco de evaluación entero es específico de Northwind. |
| `src/evaluator/comparar.py` | 153-185 (`EQUIVALENCIAS_ES_EN`: traducciones de `Categories.CategoryName` y `Customers.Country`), 138 (menciona "no customers") | Diccionario de equivalencias es-en atado a los valores reales de Northwind. |
| `src/ui/app.py` | 12-13, 50, 139, 170-175, 187-188 (`_TABLAS_NORTHWIND` lista las 8 tablas), 191-197 (consulta directa a esas tablas) | La pestaña "Explorar tablas" tiene el nombre de tablas hardcodeado. |
| `tests/test_evaluator.py` | 40, 44, 74, 83-87 | Tests que asumen `Categories.CategoryName`, el banco de Northwind, y la tabla `"Order Details"` con espacio. |
| `README.md` | 3-5, 13-38, 40-51, 62-99, 107-109 | Documentación completa: cómo obtener `northwind.db`, esquema esperado, ejemplos de preguntas, instrucciones de evaluación. |
| `docs/arquitectura.md` | sección 10 completa (líneas 405-563) y referencias sueltas en secciones 1, 6 | Historia completa de la migración a Northwind; documenta decisiones que habría que repetir para Instacart. |
| `docs/evaluaciones/*.md`, `docs/reporte_evaluacion.md` | completos | Reportes de evaluación generados corriendo el banco de Northwind; quedarán obsoletos con el cambio de dataset. |

No encontré referencias a Northwind en `src/generator/*` ni en `src/db/schema.py` (ese esquema es el del simulador viejo, no Northwind) — confirmado con `grep` sobre todo `src/`.

## 5. IA local

- **Runtime:** Ollama vía el paquete Python `ollama==0.3.3` (`src/llm/client.py:14`). Servidor systemd activo en esta máquina, escuchando en `localhost:11434` (confirmado con `curl`).
- **Modelos:** default `llama3.1:8b` (`src/llm/client.py:17`); el usuario puede cambiar con `VENTIA_LLM_MODELO` o el selector de la UI (`src/ui/app.py:59-72`). Modelos actualmente descargados en esta máquina: `qwen2.5-coder:1.5b`, `qwen2.5-coder:14b` (confirmado); README también menciona `llama3.1:8b`.
- **Cómo se construye el prompt:** dos llamadas separadas, sin streaming (`temperature=0.0` fijo, `src/llm/client.py:52-66`):
  1. `prompt_sql()` (`src/llm/prompts.py:253-256`): plantilla de sistema con el esquema completo de Northwind embebido como texto + fechas ya resueltas (`contexto_fechas`) → pregunta del usuario tal cual.
  2. `prompt_respuesta()` (`src/llm/prompts.py:277-283`): plantilla de sistema pidiendo 1-2 frases en español + la pregunta original + el resultado tabular (máx. 25 filas, `MAX_FILAS_AL_MODELO` en `src/llm/responder.py:32`, con nota de "...N filas más" si excede).
- **Cómo se genera y ejecuta el SQL:**
  - `nl2sql.generar_sql()` (`src/llm/nl2sql.py:44-48`) llama al modelo y limpia el texto (`limpiar_sql`, líneas 19-41: quita fences ```sql```, prefijo "sql", texto antes del SELECT, `;` final).
  - `safety.validar_sql()` (`src/llm/safety.py:37-59`) exige: una sola sentencia (rechaza si queda `;` interno), debe empezar con `SELECT`, y rechaza por regex de palabra completa cualquiera de: `INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, REPLACE, TRUNCATE, MERGE, UPSERT, ATTACH, DETACH, PRAGMA, VACUUM, REINDEX, GRANT`.
  - `responder.ejecutar_select()` (`src/llm/responder.py:49-60`) ejecuta con `engine.connect()` (sin abrir transacción de escritura) como segunda barrera.
- **Guardas de seguridad existentes:** las dos de arriba (whitelist de "empieza con SELECT" + blacklist de palabras + una sola sentencia, y conexión sin `commit`). **No hay** `LIMIT` forzado por código, **no hay timeout** de consulta configurado en el engine ni en `conn.execute`, y **no hay** una conexión SQLite explícitamente de solo lectura (p. ej. `sqlite3://file:...?mode=ro` o `PRAGMA query_only=1`) — la protección contra escritura depende únicamente de la validación de texto (`safety.py`) y de no hacer `commit`, no de un permiso a nivel de conexión. El brief de Instacart exige explícitamente "solo SELECT, una sola sentencia, con timeout y LIMIT, sobre conexión de solo lectura" — **timeout, LIMIT forzado y conexión read-only real no existen hoy**.
- **Streaming:** no implementado. `client.chat()` (`src/llm/client.py:52-75`) usa `ollama.chat(...)` sin `stream=True` y espera la respuesta completa.
- **Historial de conversación:** no existe. Cada pregunta en la UI (`src/ui/app.py:138-146`) es independiente; no hay estado de conversación ni `st.session_state` para historial.

## 6. Frontend y dashboard actuales

- Un único frontend: Streamlit (`src/ui/app.py`, 200 líneas), dos pestañas (`st.tabs`, línea 52):
  1. **"🤖 Pregúntale a la IA"**: input de pregunta + botón, muestra SQL/resultado/respuesta (líneas 75-97); además un botón para correr el banco de evaluación completo con barra de progreso y tabla de resultados (líneas 100-163).
  2. **"🔍 Explorar tablas"**: selector de tabla Northwind + slider de filas + `st.dataframe` simple (líneas 186-200).
- **No hay** panel de chat estilo conversación (sin historial, sin streaming — cada pregunta es un formulario de una sola vuelta).
- **No hay dashboard** de métricas/gráficas: no encontré ninguna librería de visualización (`plotly`, `matplotlib`, `altair`) en `requirements.txt` ni imports de ellas en el código (`st.dataframe`/`st.metric` son las únicas visualizaciones, ambas tablas/números planos).
- **No hay generación de PDF**: no hay ninguna librería de PDF (`reportlab`, `weasyprint`, `fpdf`, etc.) en requirements ni referencias en el código.
- No hay separación de "paneles izquierda/derecha" — es una app de pestañas secuenciales, no un layout de dos columnas persistente.

## 7. Tests y estado del repo

- **Tests ejecutados** (`.venv/bin/python -m pytest -q`): **60 passed en 4.09s**, sin fallos ni skips. Las pruebas usan una base SQLite temporal con el esquema del simulador (`tests/conftest.py:17-33`), no `northwind.db`, y sustituyen el cliente de Ollama por un doble (según `docs/arquitectura.md:109-111`) — **no** validan nada contra Northwind real ni contra Ollama real.
- **Rama actual:** `master` (no `main`). Working tree:
  - `?? data/instacart/` — CSVs de Instacart ya descargados, sin trackear.
  - `?? docs/BRIEF_INSTACART.md` — creado en esta sesión (paso 1 de esta tarea).
  - Sin cambios en archivos trackeados (`git diff --stat` vacío).
- **Último commit:** `5a3905b Migrar de datos sintéticos a Northwind Traders (datos reales)`.

## 8. Entorno

- **SO:** Fedora Linux 44 (KDE Plasma), kernel Linux 6.19.10-300.fc44.x86_64.
- **RAM:** 17 GiB totales, ~10 GiB disponibles en el momento de la auditoría (`free -h`).
- **Disco:** partición `/home` en `/dev/nvme0n1p5`, 148 GB totales, 117 GB libres (21% usado).
- **CPU:** AMD Ryzen 5 5500U, 12 hilos lógicos (`nproc`).
- **GPU:** solo la integrada del Ryzen (AMD Lucienne, `lspci`); **no hay GPU dedicada** (`nvidia-smi` no existe en el sistema). Esto es relevante para la velocidad de inferencia de Ollama con modelos más grandes (14b), que correrá en CPU.

## 9. Riesgos y deuda técnica para la migración

**Riesgos principales:**
1. **Todo el prompt del LLM está hardcodeado al esquema de Northwind** (`src/llm/prompts.py`), incluyendo fechas fijas, nombres de tabla y ejemplos de filas reales. Migrar exige reescribir esta plantilla por completo con el esquema de 6 tablas de Instacart y sus reglas (test sin productos, `days_since_prior_order` NULL/topado en 30, `order_dow`/`order_hour_of_day` anónimos) — nada de esto es reutilizable tal cual.
2. **El banco de evaluación (`banco.py`) y el comparador (`comparar.py`) son 100% Northwind**: no hay ninguna noción de "respuesta de referencia sin IA" ni de plantillas con umbrales como pide el brief para el PDF; hoy la "verdad" es solo SQL de referencia comparado contra la respuesta del LLM, un mecanismo distinto al que pide el brief (generar el PDF determinista y comparar después, fuera de alcance ahora).
3. **Sin guardas de seguridad completas para SQL generado por IA**: falta LIMIT forzado, timeout de consulta y conexión de solo lectura real (hoy solo hay validación de texto + no-commit). Con 33M+ filas, una consulta sin `LIMIT` ni timeout generada por el modelo (p. ej. un `SELECT *` accidental) puede colgar la app o agotar memoria — riesgo mucho mayor que con Northwind (609K filas).
4. **No hay dashboard, ni gráficas, ni PDF, ni chat con streaming/historial hoy** — el brief pide construir estas piezas desde cero; no hay componentes reutilizables del frontend actual salvo el patrón general "Streamlit habla directo con `src/db/engine`".
5. **SQLite + SQLAlchemy sin agregados precalculados** frente a 33M+ filas: sin evidencia empírica (no cargué los CSVs, ver sección 3) de que cumpla el objetivo de <5 s por panel con 11 necesidades simultáneas. El propio brief anticipa este problema ("se permite precalcular tablas agregadas").
6. Los CSVs de Instacart ya están en `data/instacart/raw/` (620 MB en total) pero **sin trackear en git** y sin ningún script de carga en el repo — cargarlos es trabajo pendiente completo.
7. `data/ventia.db` y todo `src/generator/` son deuda del proyecto anterior (simulador sintético): sección 9 de `docs/arquitectura.md` los documenta como "respaldo sin uso"; decidir si se eliminan o se mantienen es una decisión de producto, no técnica.

**Recomendación de motor de BD — sin haber podido correr un benchmark real (ver limitación en sección 3):**

Basado en las características conocidas de cada motor (no en una medición ejecutada en este repo):

- **DuckDB** — ventajas esperadas: columnar, vectorizado, pensado para consultas analíticas (`GROUP BY`/`JOIN`/agregaciones) sobre decenas de millones de filas en un solo proceso embebido, sin servidor; lee CSV/Parquet directo y es trivial de integrar con pandas/SQLAlchemy. Desventaja: no está instalado hoy (habría que agregarlo a `requirements.txt`, fuera del alcance de "solo auditar"); no hay evidencia en este repo de su rendimiento con este dataset específico.
- **Mantener SQLite** — ventaja: cero cambio de stack, todo el código de conexión (`src/db/database.py`) sigue funcionando. Desventaja esperada: motor de fila (row-store), sin paralelismo intra-consulta; con 11 paneles concurrentes sobre 33M+ filas es razonable esperar que algunas agregaciones (N2 canasta con pares de productos, N6 departamentos/pasillos, N9 tamaño de pedido) tarden más de los 5s objetivo sin tablas precalculadas — pero esto es una expectativa basada en el diseño del motor, no una medición hecha aquí.

Mi recomendación (con la salvedad de que no la validé con un benchmark en este repo) es **DuckDB para las consultas analíticas sobre las tablas grandes** (orders, order_products__prior/train), conservando o no SQLite como capa de compatibilidad — pero esto es una decisión de arquitectura que corresponde a la fase de diseño, no a esta auditoría, y requeriría instalar el paquete y correr un benchmark real contra los CSVs ya presentes en `data/instacart/raw/` antes de decidir con evidencia.

## 10. Preguntas abiertas para ti

1. ¿Confirmas que los CSVs ya presentes en `data/instacart/raw/` (aisles, departments, orders, products, order_products__prior, order_products__train) son la copia completa que quieres usar, o hay que re-descargar de Kaggle/yasserh para verificar checksums/licencia?
2. ¿Apruebas instalar DuckDB (u otro motor) para poder correr el benchmark real de rendimiento antes de decidir el motor de BD, dado que ahora mismo no está instalado y la regla de hoy era "no instalar paquetes"?
3. ¿`data/ventia.db` y `src/generator/` (simulador sintético) se eliminan en la migración o se mantienen como respaldo indefinidamente?
4. Los 10 casos actuales de `banco.py`/evaluador (comparación automática LLM vs. SQL de referencia) — ¿se descartan por completo, o quieres conservar ese mecanismo en paralelo al nuevo esquema de "PDF de referencia sin IA" que pide el brief?
5. ¿El dashboard/chat debe seguir siendo Streamlit, o el layout de "dos paneles" (chat izquierda, dashboard derecha) amerita evaluar otro framework (p. ej. algo con más control de layout que Streamlit, que re-ejecuta todo el script en cada interacción)?
6. Para el PDF sin IA: ¿tienes preferencia de librería (reportlab, weasyprint, fpdf2, u otra), o lo dejo abierto a evaluar en la fase de diseño?
