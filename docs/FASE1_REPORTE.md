# Fase 1 — Capa de datos Instacart (DuckDB)

Rama: `migracion-instacart`. Fecha: 2026-09-21. Fase **aditiva**: no se tocó
Northwind, el simulador ni la UI. Todo lo que sigue es la salida real de los
comandos ejecutados en esta sesión (no hay cifras copiadas a mano).

---

## 1. Comandos ejecutados y su salida real

### 1.1 Instalar DuckDB

```
$ .venv/bin/pip install duckdb
Collecting duckdb
  Downloading duckdb-1.5.5-cp312-cp312-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl.metadata (4.2 kB)
Downloading duckdb-1.5.5-cp312-cp312-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl (21.5 MB)
Installing collected packages: duckdb
Successfully installed duckdb-1.5.5
```

**Versión instalada: `duckdb==1.5.5`**, fijada en `requirements.txt`.

### 1.2 Cargar el dataset completo

```
$ time .venv/bin/python -m src.db.instacart --raw data/instacart/raw --out data/instacart.duckdb --rebuild --memory-limit 4GB

memory_limit=4GB  threads=(default de DuckDB, todos los núcleos)
[1/6] aisles                            134 filas en   0.01 s
[2/6] departments                        21 filas en   0.00 s
[3/6] products                       49,688 filas en   0.06 s
[4/6] orders                      3,421,083 filas en   3.31 s
[5/6] order_products__prior      32,434,489 filas en   1.81 s
[6/6] order_products__train       1,384,617 filas en   0.16 s

Listo: data/instacart.duckdb (161.8 MB) en 5.43 s

real    0m5,550s
user    0m24,108s
sys     0m1,501s
```

Confirmación de idempotencia (sin `--rebuild`, no reconstruye):

```
$ .venv/bin/python -m src.db.instacart --raw data/instacart/raw --out data/instacart.duckdb
data/instacart.duckdb ya existe. Usa --rebuild para reconstruirlo.
```

### 1.3 Validación completa contra el dataset real

```
$ time .venv/bin/python -m src.db.instacart_validacion --db data/instacart.duckdb --raw data/instacart/raw
```

Salida completa (22 checks PASS/FAIL + 13 INFO, cada uno con su SQL):

```
[PASS] conteo_orders                          wc(csv)=3,421,083  COUNT(*) duckdb=3,421,083  esperado=3,421,083
         SQL: SELECT COUNT(*) FROM orders
[PASS] conteo_order_products__prior           wc(csv)=32,434,489  COUNT(*) duckdb=32,434,489  esperado=32,434,489
         SQL: SELECT COUNT(*) FROM order_products__prior
[PASS] conteo_order_products__train           wc(csv)=1,384,617  COUNT(*) duckdb=1,384,617  esperado=1,384,617
         SQL: SELECT COUNT(*) FROM order_products__train
[PASS] conteo_orders_eval_set_test            COUNT(*)=75,000  esperado=75,000
         SQL: SELECT COUNT(*) FROM orders WHERE eval_set='test'
[INFO] dimensiones_aisles                     134 filas, 2 columnas (aisle_id, aisle)
         SQL: SELECT COUNT(*) FROM aisles; PRAGMA table_info('aisles')
[INFO] dimensiones_departments                21 filas, 2 columnas (department_id, department)
         SQL: SELECT COUNT(*) FROM departments; PRAGMA table_info('departments')
[INFO] dimensiones_products                   49,688 filas, 4 columnas (product_id, product_name, aisle_id, department_id)
         SQL: SELECT COUNT(*) FROM products; PRAGMA table_info('products')
[INFO] distribucion_eval_set                  prior=3,214,874, test=75,000, train=131,209
         SQL: SELECT eval_set, COUNT(*) FROM orders GROUP BY eval_set ORDER BY eval_set
[PASS] pk_unica_orders                        duplicados=0
         SQL: SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM orders
[PASS] pk_unica_products                      duplicados=0
         SQL: SELECT COUNT(*) - COUNT(DISTINCT product_id) FROM products
[PASS] pk_unica_aisles                        duplicados=0
         SQL: SELECT COUNT(*) - COUNT(DISTINCT aisle_id) FROM aisles
[PASS] pk_unica_departments                   duplicados=0
         SQL: SELECT COUNT(*) - COUNT(DISTINCT department_id) FROM departments
[PASS] pk_unica_order_products                duplicados=0
         SQL: SELECT COUNT(*) - COUNT(DISTINCT (order_id, product_id)) FROM order_products
[PASS] products_sin_aisle                     huerfanos=0
         SQL: SELECT COUNT(*) FROM products p LEFT JOIN aisles a ON a.aisle_id = p.aisle_id WHERE a.aisle_id IS NULL
[PASS] products_sin_department                huerfanos=0
         SQL: SELECT COUNT(*) FROM products p LEFT JOIN departments d ON d.department_id = p.department_id WHERE d.department_id IS NULL
[PASS] order_products_sin_order               huerfanos=0
         SQL: SELECT COUNT(*) FROM order_products op LEFT JOIN orders o ON o.order_id = op.order_id WHERE o.order_id IS NULL
[PASS] order_products_sin_product             huerfanos=0
         SQL: SELECT COUNT(*) FROM order_products op LEFT JOIN products p ON p.product_id = op.product_id WHERE p.product_id IS NULL
[PASS] reordered_rango                        filas_fuera_de_rango=0
         SQL: SELECT COUNT(*) FROM order_products WHERE reordered NOT IN (0, 1)
[PASS] order_dow_rango                        filas_fuera_de_rango=0
         SQL: SELECT COUNT(*) FROM orders WHERE order_dow NOT BETWEEN 0 AND 6
[PASS] order_hour_rango                       filas_fuera_de_rango=0
         SQL: SELECT COUNT(*) FROM orders WHERE order_hour_of_day NOT BETWEEN 0 AND 23
[PASS] days_since_prior_rango                 filas_fuera_de_rango=0
         SQL: SELECT COUNT(*) FROM orders WHERE days_since_prior_order IS NOT NULL AND days_since_prior_order NOT BETWEEN 0 AND 30
[PASS] add_to_cart_order_contiguo             pedidos_no_contiguos=0
         SQL: SELECT COUNT(*) FROM ( SELECT order_id, COUNT(*) AS n, MIN(add_to_cart_order) AS mn, MAX(add_to_cart_order) AS mx, COUNT(DISTINCT add_to_cart_order) AS distintos FROM order_products GROUP BY order_id HAVING NOT (mn = 1 AND mx = n AND distintos = n))
[PASS] dspo_null_ssi_orden1                   excepciones=0
         SQL: SELECT COUNT(*) FROM orders WHERE order_number = 1 AND days_since_prior_order IS NOT NULL
[PASS] dspo_no_null_si_no_orden1              excepciones=0
         SQL: SELECT COUNT(*) FROM orders WHERE order_number != 1 AND days_since_prior_order IS NULL
[PASS] test_sin_lineas                        excepciones=0
         SQL: SELECT COUNT(*) FROM orders o WHERE o.eval_set = 'test' AND o.order_id IN (SELECT order_id FROM order_products)
[PASS] prior_train_con_al_menos_una_linea     excepciones=0
         SQL: SELECT COUNT(*) FROM orders o WHERE o.eval_set IN ('prior', 'train') AND o.order_id NOT IN (SELECT order_id FROM order_products)
[INFO] aisles_missing_other                   [(6, 'other'), (100, 'missing')]
         SQL: SELECT aisle_id, aisle FROM aisles WHERE lower(aisle) IN ('missing', 'other')
[INFO] departments_missing_other              [(2, 'other'), (21, 'missing')]
         SQL: SELECT department_id, department FROM departments WHERE lower(department) IN ('missing', 'other')
[INFO] productos_afectados_missing_other      productos_afectados=1806
         SQL: SELECT COUNT(*) FROM products WHERE aisle_id IN (6,100) OR department_id IN (2,21)
[INFO] nulls_aisles                           sin NULLs
[INFO] nulls_departments                      sin NULLs
[INFO] nulls_products                         sin NULLs
[INFO] nulls_orders                           {'days_since_prior_order': 206209}
[INFO] nulls_order_products__prior            sin NULLs
[INFO] nulls_order_products__train            sin NULLs

22 PASS, 0 FAIL, 13 INFO
```

(Tiempo de la corrida completa de validación: ~11 s.)

### 1.4 Ficha del dataset generada por script

```
$ .venv/bin/python -m scripts.generar_ficha_dataset --db data/instacart.duckdb --raw data/instacart/raw --out docs/DATASET_INSTACART.md
Escrito docs/DATASET_INSTACART.md (22 PASS, 0 FAIL, 13 INFO)
```

### 1.5 Benchmark

```
$ .venv/bin/python -m scripts.benchmark_instacart --db data/instacart.duckdb
```
Ver salida completa en la sección 4.

---

## 2. Tabla de conteos (independiente wc -l vs. COUNT(*) vs. cifra esperada)

| Tabla | wc -l (CSV, sin cabecera) | COUNT(*) DuckDB | Esperado (brief) | Coincide |
|---|---:|---:|---:|:---:|
| `orders` | 3,421,083 | 3,421,083 | 3,421,083 | ✅ |
| `order_products__prior` | 32,434,489 | 32,434,489 | 32,434,489 | ✅ |
| `order_products__train` | 1,384,617 | 1,384,617 | 1,384,617 | ✅ |
| `orders` con `eval_set='test'` | — | 75,000 | 75,000 | ✅ |
| `aisles` | 134 | 134 | — | — |
| `departments` | 21 | 21 | — | — |
| `products` | 49,688 | 49,688 | — | — |

`orders.eval_set`: `prior=3,214,874`, `train=131,209`, `test=75,000` (suma = 3,421,083, correcto).

## 3. Resultado de cada check de validación

Ver la salida completa en la sección 1.3. Resumen:

| Bloque | Checks | PASS | FAIL |
|---|---|---:|---:|
| (a) Conteos exactos | 4 | 4 | 0 |
| (c) Integridad (PK únicas + huérfanos) | 9 | 9 | 0 |
| (d) Rangos | 5 | 5 | 0 |
| (e) Reglas de negocio del brief | 4 | 4 | 0 |
| **Total PASS/FAIL** | **22** | **22** | **0** |
| (b)+(f) Descriptivos (INFO) | 13 | — | — |

**Ningún FAIL.** Hallazgos de calidad (bloque f, informativos, no son errores):
- `aisles` tiene un valor `'other'` (aisle_id=6) y uno `'missing'` (aisle_id=100).
- `departments` tiene un valor `'other'` (department_id=2) y uno `'missing'` (department_id=21).
- 1,806 productos caen en alguno de esos 4 valores (missing/other de aisle o department).
- Único NULL real del dataset: `orders.days_since_prior_order` en 206,209 filas — coincide exactamente con el número de usuarios distintos (un primer pedido por usuario), consistente con la regla de negocio validada en (e).

## 4. Benchmark (frío/caliente/memoria)

Contexto de la corrida: RAM disponible antes 9,983 MB, después 9,235 MB. Ollama sin modelos cargados en memoria antes ni después (`GET /api/ps` → `[]`). `memory_limit=4GB`. Objetivo del brief: **<5 s en caliente**.

> **Nota sobre "frío":** cada corrida fría se lanza en un proceso Python nuevo (`subprocess`), así que no hereda ningún estado interno de DuckDB del proceso anterior. Esto **no** limpia el caché de páginas del sistema operativo (`drop_caches` requiere root, no se usó) — el archivo `.duckdb` de 162 MB probablemente ya estaba parcial o totalmente en caché de RAM del SO tras la carga inicial. "Frío" aquí es "proceso nuevo", no "disco realmente frío"; se documenta como limitación, no se presenta como algo que no es.

| Consulta | Frío (s) | Caliente 1/2/3 (s) | Mejor caliente (s) | RSS frío (MB) | RSS caliente pico (MB) | ¿Cumple <5s? |
|---|---:|---|---:|---:|---:|:---:|
| B1 recompra por producto | 0.358 | 0.375 / 0.321 / 0.323 | **0.321** | 285.4 | 315.0 | ✅ |
| B2 pedidos día×hora | 0.016 | 0.016 / 0.006 / 0.005 | **0.005** | 72.4 | 315.0 | ✅ |
| B3 pares top-1000 con soporte | 4.121 | 4.146 / 3.846 / 3.923 | **3.846** | 1,566.7 | 1,659.5 | ✅ (margen estrecho) |
| B4 variedad por usuario | 2.393 | 2.358 / 2.242 / 2.252 | **2.242** | 1,114.8 | 1,659.5 | ✅ |

**Las 4 consultas cumplen el objetivo de <5 s en caliente**, sin precalcular nada ni submuestrear, sobre las 33.8M filas completas de `order_products`.

Nota sobre B3: es la única que se acerca al límite (~3.85–4.15 s) y usa 1.5–1.7 GB de RSS pico. Su costo no escala con el total de productos porque **se restringió deliberadamente a los 1,000 productos más frecuentes** (de 49,688 productos totales) antes del self-join — esto es una decisión de diseño explícita para esta fase, no el análisis completo de canasta de mercado (N2 del brief). Sin esa restricción, el self-join sería sobre potencialmente decenas de millones de pares y el costo crecería muy por encima de los otros tres casos; se dejó documentado para que el diseño de N2 en fases futuras decida el corte correcto (top-K, soporte mínimo, o ambos) con este dato en mano.

Como las 4 pasaron, no aplica la propuesta de tabla agregada precalculada que pedía el punto 6 del prompt para el caso de falla. Sí dejo una advertencia: este benchmark corrió consultas aisladas una a la vez; un dashboard con las 11 necesidades cargando en paralelo (con Ollama corriendo al mismo tiempo, compitiendo por RAM/CPU) no está probado aquí y podría comportarse distinto — recomendaría repetir B3 y B4 con Ollama activo respondiendo antes de dar por cerrado el rendimiento del dashboard completo.

## 5. Tamaño del archivo DuckDB

- `data/instacart.duckdb`: **161.8 MB** (169,619,456 bytes).
- CSV de origen (`data/instacart/raw/`): ~680 MB combinados (577.5 MB + 24.7 MB + 109.0 MB + 2.2 MB + 2.6 KB + 0.3 KB).
- Compresión efectiva: el `.duckdb` final ocupa ~24% del tamaño de los CSV de origen (compresión columnar + tipos compactos).

## 6. Decisiones tomadas

1. **Tipos enteros compactos** verificados contra los rangos reales del dataset (no adivinados): `aisle_id`/`department_id` → `UTINYINT`; `product_id` → `USMALLINT`; `order_id`/`user_id` → `UINTEGER`; `order_number`/`order_dow`/`order_hour_of_day`/`days_since_prior_order`/`add_to_cart_order` → `UTINYINT`. Los máximos se midieron con `duckdb.read_csv_auto` antes de fijar el esquema (ver `src/db/instacart.py`, comentario de cabecera de `_DDL`).
2. **`reordered` se guarda como `UTINYINT`, no `BOOLEAN`.** Un `BOOLEAN` rechazaría un valor corrupto (p. ej. `2`) al momento de cargar, y el punto 7 del prompt pide un test donde el **validador** (no el tipo de columna) detecte esa corrupción. Con `UTINYINT` la carga acepta cualquier entero pequeño y es la consulta de rango (`reordered NOT IN (0,1)`) la que lo atrapa — que es lo que efectivamente se probó en `tests/test_instacart_loader.py::test_validador_detecta_reordered_corrupto`.
3. **Construcción atómica:** el loader escribe en `<out>.building-<pid>` y solo hace `os.replace()` al final si las 6 tablas cargaron sin excepción; si algo falla, borra el temporal. Nunca queda un `.duckdb` a medias.
4. **`memory_limit` por defecto 4GB`** (documentado en `DEFAULT_MEMORY_LIMIT` de `src/db/instacart.py`) para dejar RAM disponible a Ollama en la misma máquina, tal como pide el prompt. `--threads` no tiene default propio: si no se especifica, DuckDB usa todos los núcleos — se decidió no restringirlo por defecto porque la carga tomó 5.4 s con esa configuración y no compite con Ollama en el momento de la carga (a diferencia de una consulta en vivo del dashboard, donde sí valdría la pena revisar `--threads` en fases futuras).
5. **Vista `order_products`**, no tabla materializada: como pide el prompt, es `UNION ALL` de `order_products__prior` y `order_products__train`. Se decidió NO materializarla porque el benchmark (sección 4) muestra que las consultas sobre ella ya cumplen <5s sin ese paso extra.
6. **Validador diseñado como funciones puras reusables** (`check_conteos_exactos`, `check_integridad`, `check_rangos`, `check_reglas_negocio`, `reporte_dimensiones`, `reporte_calidad`) en vez de un único script monolítico, para poder llamarlas tanto desde el CLI como desde los tests sobre la fixture pequeña con sus propias cifras esperadas.
7. **Ficha del dataset** (`docs/DATASET_INSTACART.md`) generada por `scripts/generar_ficha_dataset.py`, que reutiliza `validar_completo()` en vez de reimplementar las consultas: una sola fuente de verdad para las cifras que aparecen en la ficha y en este reporte.

## 7. Desviaciones de este prompt (con motivo)

- **Ubicación del script de la ficha:** el prompt no especifica dónde debe vivir el script que genera `docs/DATASET_INSTACART.md`; se colocó en `scripts/generar_ficha_dataset.py` (junto al benchmark) en vez de en `src/db/`, porque no ejecuta lógica de carga ni de validación propia, solo orquesta y formatea. Motivo: mantener `src/db/` con solo los dos módulos que el prompt sí nombra explícitos (`instacart.py`, `instacart_validacion.py`).
- **"Corrida en frío" no limpia el caché de páginas del SO** (sección 4, nota). Motivo: `drop_caches` requiere privilegios de root, que no tengo en este entorno; se documenta la limitación en vez de afirmar una medición que no se hizo.
- **`scripts/__init__.py` agregado** (vacío) para poder invocar `python -m scripts.benchmark_instacart` y `python -m scripts.generar_ficha_dataset` de forma consistente con el resto del proyecto (`python -m src.llm.responder`, etc.). No estaba pedido explícitamente pero es necesario para que `-m` funcione.
- **`docs/AUDITORIA.md` y `docs/BRIEF_INSTACART.md`** (de la fase de auditoría anterior, solo lectura) seguían sin commitear desde la sesión previa. Se incluyen en el commit de esta fase porque no había ningún commit posterior donde meterlos y dejarlos sin trackear indefinidamente parecía peor que incluirlos aquí; no se modificó su contenido.

## 8. Riesgos

1. **B3 (pares de productos) tiene el margen más estrecho** frente al objetivo de 5s (3.8-4.1s) y es la consulta más cara en memoria (~1.6 GB pico). Si en el dashboard real corre junto con otras cargas (Ollama respondiendo, otros paneles) podría superar el umbral. Candidata más probable a necesitar una tabla precalculada en la fase de diseño del dashboard.
2. **B3 se restringió a los 1,000 productos más frecuentes por diseño**, no es el análisis completo de canasta de mercado. Cualquier decisión de negocio (N2) basada en este benchmark debe repetirse con el corte final que se decida (top-K distinto, soporte mínimo, etc.) antes de tomarla como definitiva.
3. **El benchmark corrió consultas aisladas, no con Ollama activo generando SQL/respuestas al mismo tiempo.** El objetivo real del dashboard es que ambos convivan; no se probó esa concurrencia en esta fase.
4. **`data/instacart.duckdb` (162 MB) y `data/instacart/raw/` (~680 MB) no están versionados** (agregados a `.gitignore`), igual que `data/northwind.db`. Cualquier persona que clone el repo necesita repetir `python -m src.db.instacart` con los CSV ya colocados a mano — no hay automatización de descarga en esta fase (no se pidió).
5. **`PRIMARY KEY` en DuckDB para tablas de millones de filas** (`orders`, `products`) construye un índice ART en la carga; no se midió el costo de mantenerlo si en el futuro se hacen cargas incrementales (hoy la carga es siempre completa desde cero).
6. Esta fase no toca el prompt del LLM ni el evaluador: el acoplamiento a Northwind identificado en `docs/AUDITORIA.md` (sección 4) sigue intacto y es trabajo de una fase posterior.

## 9. `git status`

```
En la rama migracion-instacart
Cambios no rastreados para el commit:
	modificados:     .gitignore
	modificados:     pyproject.toml
	modificados:     requirements.txt

Archivos sin seguimiento:
	docs/AUDITORIA.md
	docs/BRIEF_INSTACART.md
	docs/DATASET_INSTACART.md
	scripts/
	src/db/instacart.py
	src/db/instacart_validacion.py
	tests/fixtures/
	tests/test_instacart_integration.py
	tests/test_instacart_loader.py
```

(`data/instacart.duckdb` y `data/instacart/raw/` no aparecen: están en `.gitignore` desde esta misma fase.)

## 10. `pytest -q`

```
$ .venv/bin/python -m pytest -q
....................................................................     [100%]
68 passed in 14.28s
```

68 = 60 tests previos (Northwind/simulador, sin tocar) + 8 nuevos de esta fase (7 en `test_instacart_loader.py` + 1 en `test_instacart_integration.py`, que aquí SÍ corre de verdad porque `data/instacart.duckdb` existe en este entorno).

## 11. `pytest -m integration -q`

```
$ .venv/bin/python -m pytest -m integration -q
.                                                                        [100%]
1 passed, 67 deselected in 11.27s
```

Verificación adicional de que el skip funciona (se movió temporalmente `data/instacart.duckdb` y se restauró después):

```
$ mv data/instacart.duckdb /tmp/... && pytest -m integration -q
s                                                                        [100%]
1 skipped, 67 deselected in 0.44s
```
