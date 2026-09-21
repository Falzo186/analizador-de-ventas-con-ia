# Fase 2A — Contrato analítico y necesidades N1-N6

Rama: `migracion-instacart`. Fecha: 2026-09-21. Fase **aditiva**: Northwind,
`src/llm`, `src/evaluator`, `src/ui` y los tests existentes no se tocaron.

---

## Paso 0 — Fase 1 ya comiteada

Fase 1 se comiteó en la sesión anterior de este mismo trabajo, en la rama
`migracion-instacart`, con mensaje `Fase 1 migración Instacart: capa de
datos DuckDB` (semánticamente equivalente al `Fase 1: capa de datos
Instacart (DuckDB)` pedido; ver desviación #1 más abajo). `pytest -q` corrió
antes de ese commit y pasó 68/68. `git log --stat -1` de ese commit:

```
commit 2657cc4ed0570eb1e112ab111017a4f7973f8db8
Author: Falzo186 <a2223330180@alumnos.uat.edu.mx>
Date:   Mon Sep 21 09:19:05 2026 -0600

    Fase 1 migración Instacart: capa de datos DuckDB

    Cargador src/db/instacart.py con esquema explícito (tipos enteros compactos,
    sin autodetección) que construye data/instacart.duckdb desde los CSV de
    data/instacart/raw/ en un archivo temporal + rename atómico. Carga las 33.8M
    filas completas en 5.4s, sin submuestrear.

    Validador src/db/instacart_validacion.py: 22 checks PASS/FAIL (conteos
    cruzados wc -l vs COUNT(*), integridad de llaves y huérfanos, rangos,
    reglas de negocio del brief) + 13 INFO descriptivos, todos con su SQL.
    0 FAIL sobre el dataset completo.

    Ficha docs/DATASET_INSTACART.md generada por scripts/generar_ficha_dataset.py
    (cero cifras a mano). Benchmark scripts/benchmark_instacart.py: las 4
    consultas base (recompra, pedidos por hora, pares de productos top-1000,
    variedad por usuario) cumplen el objetivo de <5s en caliente sobre los datos
    completos.

    Tests: fixture sintética tests/fixtures/instacart_mini/ (5 usuarios, 20
    pedidos, incluye pedido 'test', primer pedido con NULL y producto 'missing'),
    tests/test_instacart_loader.py (cifras calculadas a mano + detección de
    corrupción) y tests/test_instacart_integration.py (marcado
    @pytest.mark.integration, se salta sin data/instacart.duckdb). 68/68 tests
    pasan (60 previos de Northwind/simulador sin tocar + 8 nuevos).

    Fase aditiva: no se modificó Northwind, el simulador ni la UI. Se incluyen
    también docs/AUDITORIA.md y docs/BRIEF_INSTACART.md de la fase de auditoría
    previa, que quedaron sin commitear.

    Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_012TSHMr6Qs9aRvaECEx9vDi

 .gitignore                                         |   2 +
 docs/AUDITORIA.md                                  | 157 ++++++++
 docs/BRIEF_INSTACART.md                            |  43 +++
 docs/DATASET_INSTACART.md                          |  77 ++++
 docs/FASE1_REPORTE.md                              | 246 ++++++++++++
 pyproject.toml                                     |   3 +
 requirements.txt                                   |   3 +
 scripts/__init__.py                                |   0
 scripts/benchmark_instacart.py                     | 218 +++++++++++
 scripts/generar_ficha_dataset.py                   | 129 +++++++
 src/db/instacart.py                                | 246 ++++++++++++
 src/db/instacart_validacion.py                     | 347 ++++++++++++++++++
 tests/fixtures/instacart_mini/aisles.csv           |   6 +
 tests/fixtures/instacart_mini/departments.csv      |   5 +
 tests/fixtures/instacart_mini/order_products__prior.csv | 44 +++
 tests/fixtures/instacart_mini/order_products__train.csv |  6 +
 tests/fixtures/instacart_mini/orders.csv           |  21 ++
 tests/fixtures/instacart_mini/products.csv         |   9 +
 tests/test_instacart_integration.py                |  40 ++
 tests/test_instacart_loader.py                     | 145 ++++++++
 20 files changed, 1946 insertions(+)
```

Primeras 40 líneas de `docs/DATASET_INSTACART.md`:

```
# Ficha del dataset: Instacart Online Grocery Shopping Dataset 2017

_Generado automáticamente por `scripts/generar_ficha_dataset.py` el 2026-09-21. No editar a mano: cada cifra viene de una consulta contra `data/instacart.duckdb`._

## Fuente y licencia

- **Fuente original:** The Instacart Online Grocery Shopping Dataset 2017, https://www.instacart.com/datasets/grocery-shopping-2017
- **Copia usada:** Kaggle, usuario yasserh
- **Licencia:** Licencia original de Instacart: uso no comercial.

## Limitaciones del dataset

- `orders.eval_set` puede ser `prior`, `train` o `test`. Los pedidos `test` no tienen productos en ningún archivo: se excluyen de todo análisis de productos.
- Los productos de un pedido salen de la unión de `order_products__prior` y `order_products__train` (vista `order_products`).
- `days_since_prior_order` es NULL en el primer pedido de cada usuario y está topado en 30.
- `order_dow` (0-6) y `order_hour_of_day` (0-23) son anónimos: no se sabe a qué día real corresponde el 0.
- No hay precios, fechas reales ni datos demográficos: no se puede afirmar nada sobre ingresos o dinero con este dataset.

## Tablas: filas y columnas (medidas con `COUNT(*)` / `PRAGMA table_info`)

| Tabla | Filas | Columnas |
|---|---:|---|
| `aisles` | 134 | aisle_id, aisle |
| `departments` | 21 | department_id, department |
| `products` | 49,688 | product_id, product_name, aisle_id, department_id |
| `orders` | 3,421,083 | order_id, user_id, eval_set, order_number, order_dow, order_hour_of_day, days_since_prior_order |
| `order_products__prior` | 32,434,489 | order_id, product_id, add_to_cart_order, reordered |
| `order_products__train` | 1,384,617 | order_id, product_id, add_to_cart_order, reordered |

`orders` con `eval_set = 'test'`: **75,000** (excluidos de todo análisis de productos).

## Motor y versión

- **DuckDB:** 1.5.5
- **Archivo:** `data/instacart.duckdb` (161.8 MB)

## Resultado de la validación (`src.db.instacart_validacion`)

**22 PASS, 0 FAIL, 13 INFO** — detalle completo en `docs/FASE1_REPORTE.md`.
```

---

## Arquitectura de esta fase

```
src/analytics/
├── contrato.py       # ResultadoNecesidad, saneamiento NaN/Inf, límite 500 filas/tabla
├── conexion.py        # abrir_conexion (read_only, memory_limit=4GB, threads=1), huella_bd
├── agregados.py        # agg_pares (N2) + agg_n1_producto (N1), agg_meta, --rebuild, --tabla-compromiso
├── registro.py         # id -> función calcular()
├── __main__.py          # CLI: --necesidad / --todas
├── n1_recompra.py, n2_canasta.py, n3_picos.py, n4_frecuencia.py, n5_ancla.py, n6_departamentos.py
```

Ningún archivo de `src/analytics/` importa `streamlit` ni nada de `src/ui`
(verificado con `grep -rn "streamlit\|src.ui" src/analytics/` → sin resultados).

### Por qué `threads=1` en `conexion.abrir_conexion`

Al medir determinismo se encontró que **N5 no era determinista byte a
byte**: `tasa_esperada = AVG(1.0/tam_pedido)` es un promedio de floats, y la
suma de floats no es asociativa — con DuckDB paralelizando la reducción
entre núcleos, el orden de suma cambia entre corridas y el último dígito del
resultado (`indice_ancla`, `tasa_esperada`) variaba (`5.066283224065063` vs
`5.0662832240650655`). Se fuerza `SET threads=1` en las conexiones de
análisis: el resto de las necesidades usan `SUM(entero)/SUM(entero)` o
`MEDIAN()` (exactos, no sensibles al orden), así que el único costo es la
velocidad de N5 (pasó de ~1.4s a ~3.2s en caliente, documentado en la
sección de rendimiento). Ver comentario en `src/analytics/conexion.py`.

---

## N1 — Recompra por producto

**SQL ejecutado** (4 consultas, ver `data/referencia/N1.json` para el texto
completo con los valores de percentil ya sustituidos):

```sql
-- base_producto (join con la tabla ya precalculada agg_n1_producto)
CREATE TEMP TABLE n1_base AS
SELECT n.product_id, n.lineas, n.usuarios_distintos, n.lineas_recompra,
       n.tasa_recompra, n.observables, n.abandonos, n.tasa_abandono,
       p.product_name,
       a.aisle, (p.aisle_id IN (100, 6)) AS aisle_missing_other,
       d.department, (p.department_id IN (21, 2)) AS department_missing_other
FROM agg.agg_n1_producto n
JOIN products p ON p.product_id = n.product_id
JOIN aisles a ON a.aisle_id = p.aisle_id
JOIN departments d ON d.department_id = p.department_id

-- global
SELECT SUM(lineas), SUM(lineas_recompra), SUM(lineas_recompra)*1.0/SUM(lineas),
       COUNT(*), SUM(observables)*1.0/SUM(usuarios_distintos)
FROM n1_base

-- percentiles_elegibles (con MIN_LINEAS=50, MIN_OBSERVABLES=30 ya sustituidos)
WITH elegibles AS (
    SELECT * FROM n1_base WHERE lineas >= 50 AND observables >= 30
)
SELECT COUNT(*), SUM(lineas),
       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY lineas),
       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY tasa_recompra),
       PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY tasa_recompra),
       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY tasa_abandono)
FROM elegibles

-- clasificacion (percentiles ya resueltos como literales)
WITH elegibles AS (
    SELECT * FROM n1_base WHERE lineas >= 50 AND observables >= 30
)
SELECT product_id, product_name, aisle, department, lineas, usuarios_distintos,
       tasa_recompra, observables, abandonos, tasa_abandono,
       aisle_missing_other, department_missing_other,
       (lineas >= 272.0 AND tasa_recompra >= 0.7213570675495115) AS asegurar_inventario,
       (tasa_abandono >= 0.29862579281183935 AND tasa_recompra <= 0.4906976744186046) AS revisar_retirar
FROM elegibles
ORDER BY tasa_recompra DESC, lineas DESC, product_id ASC
```

**Primeras filas:**
- `asegurar_inventario[0]`: `Half And Half Ultra Pasteurized` — 2,995 líneas, tasa_recompra=86.1%, observables=308, abandonos=64, tasa_abandono=20.8%.
- `revisar_retirar[0]`: `Thin Spaghetti` — 124 líneas, tasa_recompra=33.9%, observables=51, abandonos=36, tasa_abandono=70.6%.

**Valores clave:** tasa global de recompra=59.0%; productos elegibles=22,460; 'Asegurar inventario'=2,581; 'Revisar/retirar'=4,673; % pares observables=67.2%.

**Invariante de reconciliación:** `SUM(lineas)` de `agg_n1_producto` = 33,819,106 = total de líneas del universo (verificado en `tests/test_analytics_integration.py::test_n1_suma_lineas_totales`).

**Rendimiento:** frío 0.48s / caliente 0.29-0.31s (3 corridas), RSS pico ~120MB. Usa el agregado precalculado (ver razón en la sección de agregados más abajo: el pipeline en vivo mide ~6.0s, sobre el objetivo).

---

## N2 — Canasta

**SQL ejecutado** (el armado vive en `agregados.py`; este módulo solo lee):

```sql
-- top_50_pedidos_con_par
SELECT ap.producto_a, pa.product_name AS producto_a_nombre,
       ap.producto_b, pb.product_name AS producto_b_nombre,
       ap.pedidos_con_par, ap.soporte, ap.confianza_a_b, ap.confianza_b_a, ap.lift
FROM agg.agg_pares ap
JOIN products pa ON pa.product_id = ap.producto_a
JOIN products pb ON pb.product_id = ap.producto_b
ORDER BY ap.pedidos_con_par DESC, ap.producto_a ASC, ap.producto_b ASC
LIMIT 50

-- top_50_lift (MIN_PARES_LIFT=100 ya sustituido)
... WHERE ap.pedidos_con_par >= 100
ORDER BY ap.lift DESC, ap.pedidos_con_par DESC, ap.producto_a ASC, ap.producto_b ASC
LIMIT 50
```

**Primeras filas:**
- `top_50_pedidos_con_par[0]`: Bag of Organic Bananas + Organic Hass Avocado — 64,761 pedidos en común, soporte=1.94%, lift=2.48.
- `top_50_lift[0]`: Strawberry Rhubarb Yogurt + Mixed Berries Whole Milk Icelandic Style Skyr Yogurt — lift=154.31, 2,367 pedidos en común.

**Valores clave:** productos candidatos=842; % cobertura=51.1%; pares generados=340,461; tiempo de armado=4.86s; pares con pedidos_con_par≥100=135,967.

**Rendimiento (lectura, no arma nada):** frío 0.28s / caliente 0.13s, RSS pico ~111MB.

---

## N3 — Picos de demanda

**SQL ejecutado:**

```sql
SELECT order_dow, order_hour_of_day, COUNT(*) AS n_pedidos
FROM orders GROUP BY order_dow, order_hour_of_day ORDER BY order_dow, order_hour_of_day

SELECT order_dow, COUNT(*) AS n_pedidos FROM orders GROUP BY order_dow ORDER BY order_dow
SELECT order_hour_of_day, COUNT(*) AS n_pedidos FROM orders GROUP BY order_hour_of_day ORDER BY order_hour_of_day

WITH celdas AS (
    SELECT order_dow, order_hour_of_day, COUNT(*) AS n_pedidos
    FROM orders GROUP BY order_dow, order_hour_of_day
)
SELECT AVG(n_pedidos), PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY n_pedidos),
       PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY n_pedidos), COUNT(*)
FROM celdas
```

**Primeras filas** de `matriz_dow_hora` (orden n_pedidos desc): (Día 1, Hora 10, 55,671 pedidos, índice=2.73, pico=True); (Día 0, Hora 14, 54,552 pedidos, índice=2.68, pico=True).

**Valores clave:** día pico=1, hora pico=10, razón pico/valle=60.47x, % pedidos en ventana de 4h más cargada=32.9% (Hora 10-13).

**Invariante de reconciliación:** `SUM(n_pedidos)` de la matriz = 3,421,083 = pedidos totales (`tests/test_analytics_integration.py::test_n3_suma_pedidos_totales`).

**Rendimiento:** frío 0.22s / caliente 0.07-0.08s, RSS pico ~70MB.

---

## N4 — Frecuencia de recompra por cliente

**SQL ejecutado** (7 consultas; la más relevante):

```sql
CREATE TEMP TABLE n4_elegibles AS
WITH max_on AS (
    SELECT user_id, MAX(order_number) AS max_on FROM orders GROUP BY user_id
),
ultimo AS (
    SELECT o.user_id, o.days_since_prior_order AS ultimo
    FROM orders o JOIN max_on m ON m.user_id = o.user_id AND m.max_on = o.order_number
),
tipico AS (
    SELECT user_id, MEDIAN(days_since_prior_order) AS tipico,
           COUNT(days_since_prior_order) AS n_intervalos
    FROM orders WHERE days_since_prior_order IS NOT NULL
    GROUP BY user_id
)
SELECT t.user_id, t.tipico, u.ultimo, t.n_intervalos,
       u.ultimo * 1.0 / GREATEST(t.tipico, 1) AS razon,
       (u.ultimo = 30) AS censurado
FROM tipico t JOIN ultimo u ON u.user_id = t.user_id
WHERE t.n_intervalos >= 5
```

**Primeras filas** de `top_50_razon`: `(user_id=15858, típico=1.0, último=30, razón=30.0, censurado=True)`, `(user_id=43061, típico=1.0, último=30, razón=30.0, censurado=True)`.

**Valores clave:** usuarios evaluables=162,633; en riesgo=29,324; en riesgo censurados=16,481; % intervalos topados=11.49%; usuarios estables ante ≥3 combinaciones=18,109.

**Invariante de reconciliación:** el universo declarado (206,209 usuarios) coincide con `COUNT(DISTINCT user_id) FROM orders` (`tests/test_analytics_integration.py::test_n4_cubre_todos_los_usuarios`).

**Rendimiento:** frío 0.73s / caliente 0.56-0.58s, RSS pico ~141MB.

---

## N5 — Productos ancla

**SQL ejecutado** (7 consultas; las dos que definen la métrica):

```sql
CREATE TEMP TABLE n5_tam_pedido AS
SELECT order_id, COUNT(*) AS tam_pedido FROM order_products GROUP BY order_id

CREATE TEMP TABLE n5_base AS
SELECT op.product_id,
       COUNT(*) AS apariciones,
       SUM(CASE WHEN op.add_to_cart_order = 1 THEN 1 ELSE 0 END) AS veces_primero,
       SUM(CASE WHEN op.add_to_cart_order = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS tasa_primero,
       AVG(1.0 / tp.tam_pedido) AS tasa_esperada
FROM order_products op
JOIN n5_tam_pedido tp ON tp.order_id = op.order_id
GROUP BY op.product_id
```

**Primeras filas:**
- `top_50_volumen[0]`: Banana — 491,291 apariciones, 115,521 veces primero, tasa_primero=23.5%, índice_ancla=2.24.
- `top_50_indice_ancla[0]`: Rehab Energy Iced Tea Orangeade — 66 apariciones, tasa_primero=78.8%, índice_ancla=6.97.

**Valores clave:** productos elegibles=27,208; veces_primero total=3,346,083; top volumen=Banana; top índice_ancla=Rehab Energy Iced Tea Orangeade.

**Invariante de reconciliación:** veces_primero total = 3,346,083 = pedidos con líneas (`tests/test_analytics_integration.py::test_n5_suma_veces_primero_igual_pedidos_con_lineas`).

**Rendimiento:** frío 3.58s / caliente 3.02-3.28s, RSS pico ~383-533MB. Es la más costosa de las 6 (une 33.8M líneas contra el tamaño de cada pedido), pero corre en vivo sin agregado porque queda cómodamente bajo 5s.

---

## N6 — Departamentos y pasillos

**SQL ejecutado:**

```sql
SELECT d.department_id, d.department, COUNT(*) AS lineas,
       SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS tasa_recompra
FROM order_products op
JOIN products p ON p.product_id = op.product_id
JOIN departments d ON d.department_id = p.department_id
GROUP BY d.department_id, d.department
-- (misma consulta con aisles para por_pasillo)
```
Los cuadrantes (mediana de líneas y de tasa_recompra entre unidades
incluidas) se calculan en Python sobre estos dos result sets, no en SQL
(134/21 filas, trivial).

**Primeras filas:**
- `departamentos[0]`: produce — 9,888,378 líneas (29.2%), tasa_recompra=65.1%, cuadrante=fiel-alto-volumen.
- `pasillos[0]`: fresh fruits — 3,792,661 líneas (11.2%), tasa_recompra=71.9%, cuadrante=fiel-alto-volumen.
- `departamentos_missing_other`: 'other' (id=2, 38,086 líneas), 'missing' (id=21, 77,396 líneas).

**Valores clave:** top departamento=produce; top pasillo=fresh fruits; líneas en departamentos missing/other=115,482; líneas en pasillos missing/other=115,482.

**Invariante de reconciliación:** `SUM(departamentos.lineas) + SUM(departamentos_missing_other.lineas)` = 33,819,106 (y lo mismo para pasillos) — `tests/test_analytics_integration.py::test_n6_suma_lineas_incluye_categoria_aparte`.

**Rendimiento:** frío 1.97s / caliente 1.78-1.82s, RSS pico ~169-533MB.

---

## Tabla de compromiso de N2 (MIN_SOPORTE_PRODUCTO)

Medida con `python -m src.analytics.agregados --tabla-compromiso --valores 0.0005,0.001,0.002,0.003`, cada valor en un **proceso nuevo** (subprocess + `/usr/bin/time -v`) para que el pico de RSS no se acumule entre valores:

| MIN_SOPORTE_PRODUCTO | min_pedidos | productos candidatos | % cobertura | pares generados | tiempo | RSS pico |
|---:|---:|---:|---:|---:|---:|---:|
| 0.0005 | 1,673 | 3,403 | 75.20% | 4,567,382 | 9.34-13.27s | **~3.9 GB (excede el límite de 3GB)** |
| 0.001 | 3,346 | 1,776 | 63.86% | 1,423,979 | 6.48-9.34s | ~2.98-3.18 GB (al límite) |
| **0.002 (elegido)** | 6,692 | 842 | 51.05% | 340,461 | 4.71-7.20s | **~1.64-1.74 GB** |
| 0.003 | 10,038 | 510 | 43.13% | 126,884 | 4.01-6.14s | ~1.32-1.35 GB |

Las 4 corridas cumplen el objetivo de <120s. Solo 0.002 y 0.003 quedan con
margen claro bajo el presupuesto de <3GB de RSS; se eligió 0.002 por
maximizar cobertura (51.1%) dentro de ese margen. El tiempo/RSS varió un
poco entre las dos rondas de medición (carga del sistema), pero el orden
relativo y las conclusiones no cambian.

---

## Justificación medida de cada parámetro

| Necesidad | Parámetro | Valor | Evidencia medida |
|---|---|---|---|
| N1 | K_POST | 5 | Observables: K=3→80.2%, K=5→67.2%, K=10→46.1% (de 13,863,746 pares usuario-producto) |
| N1 | MIN_LINEAS | 50 | Líneas/producto: P25=18, mediana=63, P75=272; 50 conserva 54.7% de productos y 98.7% de líneas |
| N1 | MIN_OBSERVABLES | 30 | Observables/producto (K=5): P25=7, mediana=24; 30 conserva 45.3% de productos |
| N2 | MIN_SOPORTE_PRODUCTO | 0.002 | Tabla de compromiso arriba: único punto con RSS con margen bajo 3GB sin sacrificio drástico de cobertura |
| N2 | MIN_PARES_LIFT | 100 | pedidos_con_par entre candidatos: mediana=74, P75=172, P90=418; 100 retiene 135,967/340,461=39.9% |
| N3 | percentil_pico/valle | 0.90/0.10 | Cortes convencionales de "atípico" sobre 168 celdas día×hora (no requieren medición adicional) |
| N4 | MIN_INTERVALOS | 5 | Intervalos/usuario (universo completo orders): ≥3=100.0%, ≥5=78.9%, ≥10=49.3% |
| N4 | UMBRAL_RAZON | 2.0 | Razón entre elegibles: mediana=1.00, P75=1.62, P90=2.61; 2.0 cae entre P75 y P90 |
| N4 | UMBRAL_DIAS | 5 | Complementa UMBRAL_RAZON para no marcar diferencias triviales con típico corto |
| N5 | MIN_APARICIONES | 50 | Misma distribución que MIN_LINEAS de N1 (misma columna): 54.8% de productos, 98.7% de líneas |
| N6 | mediana por eje | medida | Mediana de líneas y de tasa_recompra calculada sobre las unidades incluidas (excluye missing/other), no un umbral fijo |

---

## Fragmento de test con esperado calculado a mano

De `tests/test_analytics_necesidades.py` (el resto de la derivación completa
está en el docstring del archivo y en los comentarios de cada test):

```python
def test_n2_par_1_3(dbs):
    con, agg = dbs
    res = n2_canasta.calcular(con, agg, min_pares_lift=1)

    # Productos 1 y 3 co-ocurren en los pedidos 101, 103, 105, 107 (todos de
    # user1) -> pedidos_con_par=4. Producto 1 aparece en 8 pedidos-con-líneas
    # en total (7 de user1 + 1 de user2); producto 3 aparece en 4 (solo
    # user1). Total de pedidos-con-líneas de la fixture = 20 (8+3+6+3).
    #   soporte = 4/20 = 0.2
    #   confianza_a_b (1->3) = 4/8 = 0.5
    #   confianza_b_a (3->1) = 4/4 = 1.0
    #   lift = (4*20) / (8*4) = 2.5
    fila = next(
        f for f in res.tablas["top_50_pedidos_con_par"]
        if {f["producto_a"], f["producto_b"]} == {1, 3}
    )
    assert fila["pedidos_con_par"] == 4
    assert fila["soporte"] == pytest.approx(0.2)
    ...
    assert fila["lift"] == pytest.approx(2.5)
```

Y su oráculo independiente en pandas (mismo archivo), que recalcula el par
desde los CSV crudos sin pasar por DuckDB:

```python
def test_n2_oraculo_pandas_par_1_3(dbs):
    orders = pd.read_csv(FIXTURE_DIR / "orders.csv")
    prior = pd.read_csv(FIXTURE_DIR / "order_products__prior.csv")
    train = pd.read_csv(FIXTURE_DIR / "order_products__train.csv")
    lineas = pd.concat([prior, train], ignore_index=True)
    por_pedido = lineas.groupby("order_id")["product_id"].apply(set)
    pedidos_con_par = sum(1 for prods in por_pedido if {1, 3}.issubset(prods))
    ...
    assert fila["pedidos_con_par"] == pedidos_con_par
    assert fila["soporte"] == pytest.approx(soporte_esperado)
    assert fila["lift"] == pytest.approx(lift_esperado)
```

---

## Desviaciones de este prompt (con motivo)

1. **PASO 0 no generó un commit nuevo de Fase 1**: ya estaba comiteado desde
   la sesión anterior con el mensaje `Fase 1 migración Instacart: capa de
   datos DuckDB` (contenido idéntico al pedido, solo el texto del mensaje
   difiere ligeramente de `Fase 1: capa de datos Instacart (DuckDB)`).
   Volver a comitear lo mismo, o hacer `amend`, iba contra las reglas de
   git de esta sesión (nunca reescribir un commit ya hecho salvo pedido
   explícito). Se pegó el `git log --stat` real de ese commit tal como pide
   el paso 0.
2. **N1 se movió a `agregados.py`, no solo N2.** El brief pedía usar
   agregados "para cualquier otra necesidad que supere 5s en caliente o
   2.5GB de RSS". Medido: el pipeline completo de N1 en vivo (base +
   abandono con censura) tardó ~6.0s (1.9s agregado base + 2.4s construir
   pares usuario-producto + 1.4s agregación de abandono), sobre el objetivo
   de 5s. Se precalculó junto con N2 en el mismo `instacart_agg.duckdb`.
3. **Se corrigieron 3 bugs de robustez encontrados por los propios tests
   nuevos**, no pedidos explícitamente pero necesarios para que el
   contrato se sostenga en casos borde:
   - N3 lanzaba `KeyError` si alguna hora 0-23 no tenía ningún pedido (no
     ocurre en el dataset real, pero sí en la fixture chica).
   - N1 generaba SQL inválido (`>= None`) si cero productos eran elegibles
     (percentiles sobre un conjunto vacío devuelven NULL).
   - N2/N5 lanzaban `IndexError` si ninguna fila superaba el umbral de la
     tabla filtrada (`MIN_PARES_LIFT`/`MIN_APARICIONES`).
   - N1 no liberaba su tabla temporal (`n1_base`), rompiendo una segunda
     llamada a `calcular()` sobre la misma conexión (relevante para
     `--todas` o un futuro dashboard de larga vida). Se agregó un test de
     regresión (`test_n1_se_puede_llamar_dos_veces_en_la_misma_conexion`).
4. **`SET threads=1`** en las conexiones de análisis (`src/analytics/
   conexion.py`), no mencionado en el prompt, necesario para que N5 sea
   determinista byte a byte (ver sección de arquitectura arriba).
5. **No se reutilizó `tests/test_instacart_loader.py`** para la nueva
   fixture, pese a que el punto 5 del prompt menciona ese nombre de
   archivo: interpretado como una plantilla heredada de la Fase 1 (ese
   archivo ya prueba el cargador/validador genéricos contra
   `instacart_mini`, y son "tests existentes" que no debía tocar). Se
   creó `tests/test_analytics_agregados.py` con el mismo espíritu (incluye
   la detección de "corrupción": un agregado rancio se rechaza) pero
   dirigido a la nueva capa de agregados de esta fase.
6. **Elegido `MIN_PARES_LIFT=1` para todos los tests con la fixture**
   (en vez del default de producción, 100): con solo 5 pares en la
   fixture, ninguno supera 100 pedidos en común.

## Riesgos

1. **N5 es la más cara en vivo (~3.0-3.6s)**: con margen frente a 5s hoy,
   pero si el dashboard corre N5 junto con otras necesidades en paralelo
   (más Ollama activo) el margen podría no sostenerse. Candidata a
   precalcularse si una fase futura lo confirma con Ollama corriendo.
2. **La clasificación de N1 puede generar más de 500 filas por grupo**
   (`asegurar_inventario`/`revisar_retirar` llegaron a 500, el límite del
   contrato, con 2,581 y 4,673 productos calificando respectivamente): se
   trunca declarando el criterio de corte, pero un consumidor (dashboard)
   que necesite el conjunto completo tendrá que re-consultar `agg_n1_producto`
   directamente.
3. **`agg_n1_producto` y `agg_pares` fijan K_POST/MIN_SOPORTE_PRODUCTO al
   construirse**: cambiar esos parámetros en producción exige `--rebuild`
   (varios segundos), no es instantáneo como los demás parámetros
   (MIN_LINEAS, UMBRAL_RAZON, etc., que sí se pueden variar en cada llamada
   sin reconstruir nada).
4. **N2 se restringe a 842 productos candidatos (51% de las incidencias
   producto-pedido)**: cualquier decisión de negocio sobre canasta debe
   tener presente que el 49% restante de las incidencias no participa en
   ningún par reportado.
5. **El benchmark de rendimiento no se corrió con Ollama activo** generando
   SQL/respuestas al mismo tiempo (mismo riesgo señalado en
   `docs/FASE1_REPORTE.md`): la concurrencia real del dashboard+chat no
   está probada todavía.
6. Esta fase no toca `src/llm/prompts.py`: el esquema que ve la IA sigue
   siendo el de Northwind. Eso es trabajo de una fase posterior.

---

## `git status`

```
 M .gitignore
?? docs/NECESIDADES.md
?? src/analytics/
?? tests/fixtures/instacart_needs/
?? tests/helpers_analytics.py
?? tests/test_analytics_agregados.py
?? tests/test_analytics_contrato.py
?? tests/test_analytics_determinismo.py
?? tests/test_analytics_honestidad.py
?? tests/test_analytics_integration.py
?? tests/test_analytics_necesidades.py
```
(`data/instacart_agg.duckdb` y `data/referencia/*.json` no aparecen: están
en `.gitignore` desde esta fase — `*.duckdb` ya cubría el agregado, se
agregó `data/referencia/`.)

## `pytest -q`

```
........................................................................ [ 52%]
................................................................         [100%]
136 passed in 22.97s
```
136 = 68 previos (Fase 1, sin tocar) + 68 nuevos de esta fase (15 en
`test_analytics_necesidades.py`, 30 en `test_analytics_contrato.py`, 4 en
`test_analytics_agregados.py`, 6 en `test_analytics_determinismo.py`, 7 en
`test_analytics_honestidad.py`, 6 en `test_analytics_integration.py`).

## `pytest -m integration -q`

```
......                                                                   [100%]
6 passed, 130 deselected in 15.60s
```
(1 de Fase 1 + 5 de esta fase, todas reconciliando contra el dataset real
completo.) Verificado también que se saltan limpiamente si falta
`data/instacart.duckdb`.
