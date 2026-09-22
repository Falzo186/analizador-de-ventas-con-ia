# Fase 2B — Correcciones a 2A y necesidades N7-N11 (referencia sin IA)

Rama: `migracion-instacart`. Fecha: 2026-09-22. Fase **aditiva**: Northwind,
`src/llm`, `src/evaluator`, `src/ui` no se tocaron. Los tests existentes de
Fase 2A se modificaron solo donde C1/C2/C4/C5 lo exigían (listado en
"Desviaciones" más abajo); ninguno se borró.

---

## Paso 0

### 0a — Fase 2A ya comiteada

`git status` al arrancar esta sesión mostró Fase 2A ya comiteada como
`e383f1e` ("Fase 2A: contrato analítico y necesidades N1-N6"), con
`pytest -q` en 136/136 antes de ese commit. No había cambios de 2A sin
comitear (los `M`/`??` pendientes eran ya trabajo de 2B). `git log --stat -1`
de ese commit:

```
commit e383f1e4e01109a567a8457d7741bb4a3bf046d7
Author: Falzo186 <a2223330180@alumnos.uat.edu.mx>
Date:   Mon Sep 21 10:11:11 2026 -0600

    Fase 2A: contrato analítico y necesidades N1-N6

    Contrato común src/analytics/contrato.py (ResultadoNecesidad serializable a
    JSON, saneamiento de NaN/Inf, límite de 500 filas/tabla con criterio de
    corte declarado) y src/analytics/conexion.py (DuckDB read_only,
    memory_limit=4GB, threads=1 para determinismo byte a byte).

    Agregados precalculados en src/analytics/agregados.py (data/instacart_agg.duckdb,
    ignorado por git): agg_pares para N2 y agg_n1_producto para N1 (el pipeline
    en vivo de N1 medía ~6.0s, sobre el objetivo de 5s). agg_meta detecta un
    agregado rancio si la huella de la BD fuente cambió. Tabla de compromiso de
    N2 con 4 valores de MIN_SOPORTE_PRODUCTO medidos en procesos aislados.

    N1-N6 implementadas con universos, parámetros justificados con la
    distribución real medida (K_POST, MIN_LINEAS, MIN_OBSERVABLES,
    MIN_SOPORTE_PRODUCTO, MIN_PARES_LIFT, MIN_INTERVALOS, UMBRAL_RAZON/DIAS,
    MIN_APARICIONES) y hallazgos de plantilla sin cifras hardcodeadas. Las 6
    corren en <5s en caliente sobre el dataset completo (33.8M líneas). CLI
    python -m src.analytics --necesidad/--todas. docs/NECESIDADES.md documenta
    la definición formal de cada métrica.

    Tests: fixture tests/fixtures/instacart_needs/ (4 usuarios cubriendo pedido
    test, primer pedido NULL, intervalo topado en 30, producto siempre primero,
    par co-ocurrente, producto aisle 'missing', censura de N1) con valores
    esperados calculados a mano y oráculos independientes en pandas. Tests de
    contrato, determinismo (dos corridas, mismo hash), honestidad (sin lenguaje
    de dinero en hallazgos), agregados (detecta agregado rancio) e integración
    (reconcilian con Fase 1: N1/N6 suman 33,819,106 líneas, N3 suma 3,421,083
    pedidos, N4 cubre 206,209 usuarios, N5 suma 3,346,083 veces_primero).
    136/136 tests pasan (68 previos sin tocar + 68 nuevos). Se corrigieron 3
    bugs de robustez en casos borde encontrados por los propios tests nuevos.

    Fase aditiva: Northwind, src/llm, src/evaluator, src/ui y los tests
    existentes no se modificaron.

    Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_012TSHMr6Qs9aRvaECEx9vDi

 .gitignore                                         |   1 +
 docs/FASE2A_REPORTE.md                             | 566 +++++++++++++++++++++
 docs/NECESIDADES.md                                | 213 ++++++++
 src/analytics/__init__.py                          |   7 +
 src/analytics/__main__.py                          |  47 ++
 src/analytics/agregados.py                         | 431 ++++++++++++++++
 src/analytics/conexion.py                          |  53 ++
 src/analytics/contrato.py                          | 167 ++++++
 src/analytics/n1_recompra.py                       | 258 ++++++++++
 src/analytics/n2_canasta.py                        | 197 +++++++
 src/analytics/n3_picos.py                          | 171 +++++++
 src/analytics/n4_frecuencia.py                     | 208 ++++++++
 src/analytics/n5_ancla.py                          | 193 +++++++
 src/analytics/n6_departamentos.py                  | 197 +++++++
 src/analytics/registro.py                          |  42 ++
 tests/fixtures/instacart_needs/...                 | (14 archivos)
 tests/test_analytics_*.py                          | (6 archivos)
 24 files changed, 3061 insertions(+)
```

### 0b — Verificación de percentiles de N1 (`percentiles_elegibles`)

El bloque de clasificación de `docs/FASE2A_REPORTE.md` mostraba los
literales `272.0 / 0.7214 / 0.4907 / 0.2986` ya sustituidos en el SQL de
`asegurar_inventario`/`revisar_retirar`. Se verificó si esos literales
correspondían a los percentiles calculados **sobre productos ELEGIBLES**
(como exige el código, CTE `elegibles` en `n1_recompra.py`) o sobre
**todos** los productos.

Consulta ejecutada contra `data/instacart.duckdb` (MIN_LINEAS=50,
MIN_OBSERVABLES=30, los defaults reales de `n1_recompra.py`):

```sql
WITH elegibles AS (
    SELECT * FROM n1_base WHERE lineas >= 50 AND observables >= 30
)
SELECT COUNT(*), SUM(lineas),
       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY lineas),
       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY tasa_recompra),
       PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY tasa_recompra),
       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY tasa_abandono)
FROM elegibles
```

**Resultado real:** `(22460, 33046554, 915.0, 0.5783244919978421,
0.33880965075979735, 0.6875)` — es decir, P75(líneas)=**915.0**,
P75(tasa_recompra)=**0.5783**, P25(tasa_recompra)=**0.3388**,
P75(tasa_abandono)=**0.6875** entre los 22,460 productos elegibles. Estos
son EXACTAMENTE los literales que el código de `n1_recompra.py` sustituye
en la clasificación (`lineas >= 915.0 AND tasa_recompra >= 0.578...`).

Para contraste, P75 de líneas entre **TODOS** los productos (sin filtrar
por elegibilidad):

```sql
SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY lineas) FROM n1_base
```
Resultado: **272.0**.

**Conclusión:** `272.0` es el P75 de líneas sobre TODOS los productos, no
sobre los elegibles — coincide con el literal que aparecía mal transcrito
en el texto de `docs/FASE2A_REPORTE.md` (no en el código). Se confirmó
releyendo `n1_recompra.py` con `git diff` (sin cambios en ese archivo desde
el commit `e383f1e`) que el SQL real ejecutado por `calcular()` **siempre**
calculó los percentiles sobre el CTE `elegibles`, correctamente. Los
tamaños de grupo ya reportados en 2A (2,581 'Asegurar inventario' / 4,673
'Revisar/retirar') ya eran correctos porque salían de `calcular()`, no del
texto del reporte. **No hubo bug de código: fue un error de transcripción
en el reporte**, ya corregido con una nota en `docs/FASE2A_REPORTE.md`
(línea ~199) que apunta a esta sección. Se re-ejecutó `n1_recompra.calcular()`
completo para confirmar que las cifras vigentes (22,460 elegibles; 2,581 /
4,673) siguen siendo las mismas: ver sección "Regresión N1-N6" más abajo.

---

## C1 — N4: `intervalo_típico` excluye el intervalo del último pedido

**Antes:** `intervalo_típico` = MEDIAN(`days_since_prior_order`) sobre
**todos** los intervalos no nulos del usuario, incluido el intervalo de su
último pedido (el mismo que se compara contra "típico" para detectar
anomalía) — esto sesga la mediana hacia el propio valor que se evalúa.

**Después:** `intervalo_típico` = MEDIAN sobre los intervalos **previos**
al último pedido (`order_number <> MAX(order_number)`); `MIN_INTERVALOS`
se cuenta sobre ese conjunto reducido.

**Test con esperado a mano** (`tests/test_analytics_necesidades_b.py`,
`test_n4_ultimo_intervalo_excluido_de_tipico`; ver fragmento completo en la
sección de tests más abajo): usuario 3 de la fixture con intervalos previos
`[7, 20]` (mediana=13.5) y último intervalo=3 días; si el último entrara en
la mediana, `[3,7,20]` daría mediana=7, cambiando el resultado.

**Antes/después medidos contra el dataset real completo** (dos consultas
SQL independientes, `MIN_INTERVALOS=5`, `UMBRAL_RAZON=2.0`,
`UMBRAL_DIAS=5`, los defaults reales):

| Métrica | ANTES (bug) | DESPUÉS (C1) |
|---|---:|---:|
| Usuarios evaluables | 162,633 | 146,468 |
| Usuarios excluidos por MIN_INTERVALOS | 43,576 | 59,741 |
| Usuarios en riesgo | 29,324 | 32,507 |
| Usuarios en riesgo censurados (último=30) | 16,481 (56.2%) | 18,091 (55.7%) |

El "antes" reproduce exactamente los valores publicados en
`docs/FASE2A_REPORTE.md` (162,633 / 29,324 / 16,481), confirmando que esa
réplica del bug es fiel. El hallazgo de N4 ahora menciona explícitamente el
% de "en riesgo" con último=30 (censurado): *"De esos 32,507 usuarios en
riesgo, 18,091 (55.7%) tienen su último intervalo topado en 30 (censurado):
su intervalo real podría ser mayor a 30 días"*.

---

## C2 — N5: `usuarios_distintos`, `usuarios_primero` y `MIN_USUARIOS_ANCLA`

**Antes:** elegibilidad solo exigía `MIN_APARICIONES>=50`. Un producto con
pocas apariciones pero todas del mismo usuario recurrente podía tener
`tasa_primero`/`índice_ancla` altísimos sin ser una señal poblacional real.

**Después:** se agregan `usuarios_distintos` (leído de `agg_n1_producto`,
ya precalculado por N1 — evita un `COUNT(DISTINCT user_id)` en vivo sobre
33.8M líneas, ~5.9s) y `usuarios_primero` (`COUNT(DISTINCT user_id)` entre
líneas con `add_to_cart_order=1`, por producto). Elegibilidad ahora exige
`MIN_APARICIONES>=50` **y** `usuarios_primero>=MIN_USUARIOS_ANCLA`.

**MIN_USUARIOS_ANCLA=10**, medido sobre la distribución de
`usuarios_primero` entre los 42,188 productos que fueron primero al menos
una vez: mínimo=1, P25=2, mediana=6, P75=22. 10 cae entre la mediana y P75.

**Top 10 por índice_ancla ANTES (solo MIN_APARICIONES=50):**

| # | Producto | apariciones | índice_ancla |
|---|---|---:|---:|
| 1 | Rehab Energy Iced Tea Orangeade | 66 | 6.97 |
| 2 | Graduates Turkey Sticks | 105 | 5.07 |
| 3 | Original Smoked Snack Sticks | 67 | 4.57 |
| 4 | Guac-A-Mole Corn Tortilla Chips | 85 | 4.56 |
| 5 | Rice Bowl Chicken Fajita Frozen Dinner | 97 | 3.95 |
| 6 | Trumoo Choc Milk Hg | 68 | 3.81 |
| 7 | Authentic Asia Shrimp Wonton Soup With Noodles | 86 | 3.78 |
| 8 | Organics Second Generation Grain Free Cat Food | 68 | 3.75 |
| 9 | Smooth & Sweet Citrus Punch | 78 | 3.70 |
| 10 | Green with Honey Iced Tea | 53 | 3.67 |

**Top 10 por índice_ancla DESPUÉS (C2, con MIN_USUARIOS_ANCLA=10):**

| # | Producto | apariciones | usuarios_distintos | usuarios_primero | índice_ancla |
|---|---|---:|---:|---:|---:|
| 1 | Homestyle Orange Juice | 317 | 47 | 18 | 3.64 |
| 2 | TAB | 163 | 28 | 18 | 3.42 |
| 3 | Tidy Cats 24/7 Performance Cat Litter | 60 | 24 | 11 | 3.11 |
| 4 | Lemon Lime Seltzer Water | 110 | 27 | 10 | 3.10 |
| 5 | Paneer Tikka Masala With Rajma & Onion Basmati Rice | 125 | 50 | 12 | 2.99 |
| 6 | Greek Seasoning, All Purpose | 53 | 42 | 14 | 2.82 |
| 7 | Signature Blend K-Cup Packs Coffee | 306 | 111 | 28 | 2.77 |
| 8 | 100% Lactose Free Ultra-Pasteurized Milk With Vitamin D | 142 | 46 | 16 | 2.77 |
| 9 | Mellow Mango Sweet Dreams | 121 | 29 | 12 | 2.72 |
| 10 | Diet Pepsi Soda | 299 | 68 | 19 | 2.71 |

**Productos elegibles:** 17,170 después de C2 (antes: 27,208 con solo
MIN_APARICIONES). **Del top 50 anterior, 40 de 50 salen del top 50
nuevo** — la señal cambia sustancialmente: los productos con
`índice_ancla` extremo pero pocos usuarios distintos (p. ej. "Rehab Energy
Iced Tea Orangeade": 66 apariciones concentradas en muy pocos usuarios)
desaparecen del top.

---

## C3 — Verificación cruzada de agregados

`tests/test_analytics_cruzada.py` (marcado `@pytest.mark.integration`, 2
tests) corre contra el dataset real completo y **pasa**
(`pytest -m integration -q`: 14 passed, incluyendo estos 2).

### `agg_n1_producto` vs. pipeline en vivo (30 productos)

Selección determinista: 15 de mayor volumen (`lineas DESC, product_id ASC`)
+ 15 más elegidos que no estén ya en esa lista (`usuarios_distintos DESC,
product_id ASC`). Tolerancia: enteros 0, floats 1e-9. Muestra (4 de los 30
comparados, valores completos):

| product_id | producto | agregado (líneas / recompra) | en vivo (líneas / recompra) | match |
|---:|---|---|---|---|
| 4605 | Yellow Onions | 76,929 / 45,817 | 76,929 / 45,817 | ✓ |
| 13176 | Bag of Organic Bananas | 394,930 / 329,275 | 394,930 / 329,275 | ✓ |
| 24852 | Banana | 491,291 / 415,166 | 491,291 / 415,166 | ✓ |
| 49683 | Cucumber Kirby | 99,728 / 69,022 | 99,728 / 69,022 | ✓ |

Las 30 filas (incluyendo `usuarios_distintos`, `tasa_recompra`,
`observables`, `abandonos`, `tasa_abandono`) coinciden exactamente contra
el cálculo en vivo (sin usar `agg_n1_producto`, con control de censura
recalculado desde `orders`/`order_products`).

### `agg_pares` vs. recuento directo (10 top por `pedidos_con_par` + 10 top por `lift`)

Muestra (3 de los 20 pares comparados):

| par | agregado (pedidos_con_par / lift) | directo (n_par) | match |
|---|---|---:|---|
| (13176, 47209) | 64,761 / 2.4842 | 64,761 | ✓ |
| (13176, 21137) | 64,702 / 1.9893 | 64,702 | ✓ |
| (21137, 24852) | 58,330 / 1.4416 | 58,330 | ✓ |

Los 20 pares (10 por `pedidos_con_par` + 10 por `lift`) coinciden
exactamente en `pedidos_con_par`, `confianza_a_b`, `confianza_b_a` y
`lift` (tolerancia 1e-9) contra el recuento directo desde
`order_products`.

---

## C4 — Huella de contenido (`huella_contenido`)

`ResultadoNecesidad.a_dict()` agrega `huella_contenido`: sha256 de la
serialización canónica (`json.dumps(sort_keys=True)`, sin indentación) del
resto del documento, **excluyendo** los campos volátiles
`CAMPOS_VOLATILES = ("fecha_generacion", "duracion_s")` (documentados en
`docs/NECESIDADES.md`). `tests/test_analytics_determinismo.py` corre cada
una de las 11 necesidades DOS veces sobre la misma fixture, compara
`huella_contenido` entre ambas corridas, y además serializa el resto del
documento (sin campos volátiles) y compara **texto completo**, no solo el
hash — así una colisión de hash no pasaría inadvertida. Ver fragmento
completo del test en la sección de tests.

---

## C5 — Higiene de tests e invariante de N4 reforzado

**¿Cuál de los 6 tests de `test_analytics_integration.py` no está marcado
`@pytest.mark.integration`?** Ninguno individualmente decorado — el
archivo declara `pytestmark = pytest.mark.integration` a nivel de módulo
(línea 25), que aplica la marca a las 6 funciones automáticamente (ahora 7,
con el test nuevo de C5). Se verificó con
`pytest -m integration -q` (ver salida completa más abajo: 14 passed,
incluye las 7 de este archivo + 2 de `test_analytics_cruzada.py` + 5 de
`test_analytics_integration_b.py`) y con `pytest --collect-only -m "not
integration" tests/test_analytics_integration.py` (0 tests recolectados).
**Justificación: ya cumple el invariante, se documenta el mecanismo
(marca a nivel de módulo) en vez de decorar cada función individualmente.**

**Refuerzo del invariante de N4** (`test_n4_evaluables_mas_excluidos_reconcilia`,
nuevo): dos consultas independientes entre sí y de `n4_frecuencia.calcular()`
— (a) `COUNT(DISTINCT user_id) FROM orders` (206,209), (b) usuarios con
`>=MIN_INTERVALOS(5)` intervalos previos a su último pedido, recalculado
desde cero con su propia CTE (146,468) — confirman
`146,468 + 59,741 == 206,209`.

---

## N7 — Evolución por número de pedido

**SQL ejecutado** (4 consultas; lee tablas precalculadas en `agregados.py`
por el motivo de rendimiento explicado abajo):

```sql
-- invariante_totales
SELECT COUNT(*), SUM(tamano) FROM agg.agg_pedido

-- por_numero_pedido
SELECT * FROM agg.agg_n7_por_k WHERE usuarios_n >= 100 ORDER BY k

-- pareado_1_vs_10
SELECT * FROM agg.agg_n7_pareado

-- reordered_en_primer_pedido
SELECT lineas_k1, lineas_k1_reordered FROM agg.agg_n7_reordered_k1
```

**Primeras filas** de `por_numero_pedido`:
- `k=1`: 206,209 pedidos, usuarios_n=206,209, tamaño_medio=10.08, tasa_recompra=0.0%, productos_nuevos=10.08, departamentos_distintos=4.77.
- `k=2`: 206,209 pedidos, tamaño_medio=9.93, tasa_recompra=27.2%, productos_nuevos=7.23.
- `k=3`: 206,209 pedidos, tamaño_medio=9.94, tasa_recompra=38.6%, productos_nuevos=6.10.

`pareado_1_vs_10` (1 fila, agregada sobre 107,438 usuarios con ambos
pedidos): `diff_tamano_media=-0.288 (std=7.53)`, `diff_tasa_recompra_media=
+0.647 (std=0.284)`, `diff_productos_nuevos_media=-6.71 (std=7.67)`,
`diff_departamentos_distintos_media=-0.077 (std=2.58)`.

**Valores clave:** pedidos totales=3,346,083; líneas totales=33,819,106;
usuarios en comparación pareada=107,438; diff_tamano_media=-0.288.

**Hallazgos:**
- Se muestran 100 valores de k con MIN_USUARIOS_K=100; en k=1 tamaño medio=10.08, tasa_recompra=0.0%; hacia k alto la recompra sube y la variedad de departamentos se mantiene relativamente estable.
- Pareado (n=107,438): tamaño -0.29 líneas, tasa_recompra +64.7 puntos, productos nuevos -6.71, departamentos -0.08. Es la comparación válida de "pedido 1 vs pedido 10" del brief.

**Medido, no asumido:** 0 de 2,078,068 líneas en order_number=1 tienen `reordered=1` (0.0000%).

**Invariante:** SUM(pedidos por k, TODOS los k) = 3,346,083 = SUM(tamano) de agg_pedido = 33,819,106 de líneas. Verificado en `test_n7_suma_pedidos_y_lineas` con dos consultas independientes contra `orders`/`order_products`.

**Rendimiento:** frío (subprocess) 0.27s / caliente (3 corridas) 0.12-0.13s, RSS pico ~77MB. **Precomputado en `agregados.py`** porque el pipeline en vivo (curva por k + pareado sobre 33.8M líneas) mostró el mismo patrón de degradación por presión de memoria bajo `threads=1`/4GB que motivó precomputar N1/N2 en 2A; `agregados.py` corre multi-hilo sin ese problema (5.516s → 0.128s, mismo orden de mejora que N1).

---

## N8 — Rutinarios vs exploradores

**SQL ejecutado** (9 consultas; las que definen la clasificación):

```sql
CREATE TEMP TABLE n8_clasificables AS
SELECT user_id, pedidos_totales, mediana_intervalo, tasa_recompra_historial,
       pedidos_en_ventana, productos_distintos_ventana, lineas_ventana,
       lineas_recompra_ventana, lineas_nuevas_ventana,
       productos_distintos_ventana * 1.0 / lineas_ventana AS indice_novedad,
       lineas_ventana * 1.0 / pedidos_en_ventana AS tamano_medio_ventana,
       lineas_recompra_ventana * 1.0 / lineas_ventana AS tasa_recompra_ventana
FROM agg.agg_usuario
WHERE pedidos_en_ventana >= 5

SELECT PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY indice_novedad),
       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY indice_novedad)
FROM n8_clasificables
```

**Primeras filas** de `perfil_por_clase`:
- explorador (n=44,041): tamaño medio=8.36, mediana_intervalo=12.5d, pedidos/usuario=17.0, tasa_recompra_histórica=31.5%.
- intermedio (n=87,245): tamaño medio=10.76, mediana_intervalo=12.0d, pedidos/usuario=18.8, tasa_recompra_histórica=48.5%.
- rutinario (n=43,786): tamaño medio=10.30, mediana_intervalo=11.0d, pedidos/usuario=19.3, tasa_recompra_histórica=64.3%.

**Valores clave:** usuarios clasificables=175,072; excluidos por W=31,137; corte rutinario (P25)=0.584; corte explorador (P75)=0.792; Pearson(índice_novedad, tamaño)=-0.095; consistencia productos_distintos==reordered=0: 175,072/175,072 (100.0%).

**Hallazgos:**
- 175,072 de 206,209 usuarios (84.9%) clasificables con W=5; 31,137 excluidos.
- Cortes por construcción: rutinario<=0.584 (P25), explorador>=0.792 (P75).
- Consistencia medida (no asumida): 100.0% de coincidencia entre productos_distintos y líneas reordered=0 en la ventana.
- Confusor Pearson=-0.095 (correlación débil y negativa entre índice_novedad y tamaño medio de pedido: en este dataset NO es un confusor fuerte, se reporta igual por transparencia).

**Corrección aplicada durante esta fase:** `JUSTIF_W` citaba "197,523 de
206,209 (95.8%)" — no coincidía con la medición real (175,072 / 84.9%,
verificado con dos consultas SQL directas, ver detalle en "Desviaciones").
Corregido antes de cerrar la fase.

**Invariante:** 175,072 + 31,137 = 206,209. Verificado en `test_n8_clasificados_mas_excluidos`.

**Rendimiento:** frío 0.60s / caliente (3 corridas) 0.83-0.85s, RSS pico ~108MB.

---

## N9 — Tamaño de pedido

**SQL ejecutado** (estadísticos globales + histograma; los 4 factores usan la misma función `_factor`):

```sql
SELECT COUNT(*), SUM(tamano), AVG(tamano), MEDIAN(tamano), MODE(tamano),
       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY tamano),
       PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY tamano),
       PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY tamano)
FROM agg.agg_pedido

SELECT CASE WHEN tamano > 35 THEN 36 ELSE tamano END AS tamano_bin, COUNT(*) AS pedidos
FROM agg.agg_pedido GROUP BY 1 ORDER BY 1

-- factor (hora/día/intervalo/order_number en cubos), ejemplo hora:
SELECT ap.order_hour_of_day AS valor, COUNT(*) AS pedidos,
       AVG(ap.tamano) AS tamano_medio_bruto,
       AVG(ap.tamano - ua.tamano_medio_usuario) AS desviacion_intra_usuario
FROM agg.agg_pedido ap JOIN n9_usuario_avg ua ON ua.user_id = ap.user_id
GROUP BY 1 ORDER BY 1
```

**Primeras filas** de `histograma_tamano`: tamaño=1→163,593 pedidos;
tamaño=2→194,361; tamaño=3→215,060; tamaño=4→230,299.
`factor_hora[0]` (hora=0): 21,393 pedidos, bruto=10.27, intra-usuario=+0.36.
`factor_intervalo`: `0-3`→611,003 pedidos, bruto=7.85, intra-usuario=-1.22
(pedidos cortos-intervalo son más chicos incluso controlando por usuario);
`15-29`→455,794, bruto=10.67, intra-usuario=+0.48.

**Valores clave:** tamaño medio=10.11, mediana=8.0, TAM_MAX(P99)=35;
% pedidos<=5: 31.1%; <=10: 62.2%; <=15: 80.6%; <=20: 90.5%.

**Hallazgos:**
- 3,346,083 pedidos: media 10.11, mediana 8, moda 5, P75=14, P90=20, P99=35.
- % acumulado (sin montos): 5 productos→31.1%; 10→62.2%; 15→80.6%; 20→90.5%.
- Desviación intra-usuario (175,072 usuarios con >=5 pedidos): rango por hora -0.33 a +0.77; por intervalo -1.22 a +0.48 — el intervalo desde el pedido previo es el factor con mayor efecto medido, incluso controlando por usuario.

**Invariante:** suma histograma = 3,346,083; SUM(tamano) de agg_pedido = 33,819,106. Verificado en `test_n9_histograma_y_suma_tamano` con dos consultas independientes.

**Rendimiento:** frío 1.47s / caliente (3 corridas) 1.26-1.87s, RSS pico ~284MB.

---

## N10 — Final del carrito / impulso

**SQL ejecutado** (una sola pasada por las líneas, tramo×departamento y tramo se derivan juntos en Python para no repetir el join pesado):

```sql
WITH lineas AS (
    SELECT op.reordered, p.department_id,
           (op.add_to_cart_order - 1.0) / (ap.tamano - 1) AS pos_norm
    FROM order_products op
    JOIN agg.agg_pedido ap ON ap.order_id = op.order_id
    JOIN products p ON p.product_id = op.product_id
    WHERE ap.tamano >= 2
),
con_tramo AS (
    SELECT reordered, department_id,
           LEAST(CAST(FLOOR(pos_norm * 5) AS INTEGER), 4) AS tramo
    FROM lineas
)
SELECT ct.tramo, d.department, COUNT(*) AS lineas, SUM(ct.reordered) * 1.0 / COUNT(*) AS tasa_recompra
FROM con_tramo ct JOIN departments d ON d.department_id = ct.department_id
GROUP BY ct.tramo, d.department ORDER BY ct.tramo, d.department
```

**Primeras filas** de `tasa_recompra_por_tramo`: tramo 0→7,441,783 líneas,
tasa=69.6%; tramo 1→6,059,635, tasa=66.6%; tramo 2→6,094,628, tasa=61.0%.
`top_50_indice_final[0]`: Kikkoman Soy Sauce, apariciones=2,
apariciones_ultimo_tramo=2, share_final=100%, esperado_final=20%,
índice_final=5.00.
`top_50_indice_final_tasa_baja[0]`: Everyday Shea Lemon & Lavender
Conditioner & Detangler For Babies And Up, índice_final=2.56,
tasa_recompra=11.3%.

**Valores clave:** líneas incluidas=33,655,513; excluidas (MIN_TAM)=163,593; productos elegibles=27,130; top índice_final=Kikkoman Soy Sauce.

**Hallazgos:**
- Universo: 33,655,513 líneas de 3,182,490 pedidos con tamaño>=2; se excluyen 163,593 líneas de 163,593 pedidos de 1 producto.
- Tasa de recompra CAE monótonamente del inicio al final del carrito: tramo 0: 69.6% → tramo 4: 45.6% — consistente con que lo "seguro"/habitual se agrega primero y lo exploratorio al final.
- 27,130 productos elegibles (MIN_APARICIONES>=50, MIN_USUARIOS>=10).
- Entre elegibles con tasa_recompra baja (P25), el de mayor índice_final es un candidato "de impulso puro, no recompra habitual" (tasa_recompra=11.3%).

**Invariante:** 33,655,513 + 163,593 = 33,819,106. Verificado en `test_n10_incluidas_mas_excluidas`.

**Rendimiento:** variable con el estado de caché del SO — frío (subprocess, primera ejecución de la sesión) 3.28s; en corridas posteriores dentro de la misma sesión, con el disco/buffer pool bajo presión por las mediciones anteriores, se observaron picos de hasta 5.95s y 6.10s (en la corrida completa de `--todas`); en caliente (mismo proceso, corridas repetidas) se estabiliza en 2.8-2.9s. RSS pico ~440MB. Ver "Riesgos" — es la única necesidad de N7-N11 cuyo tiempo cruza el objetivo de 5s en el peor caso medido, pese a usar el agregado precalculado, porque el join tramo×departamento recorre las 33.8M líneas de `order_products` en vivo (no solo el agregado).

---

## N11 — Predicción del siguiente pedido

**SQL ejecutado** (interfaz `predecir_usuario` + selección de DECAY, ver `n11_prediccion.py` para las 16 consultas de métricas):

```sql
-- techo alcanzable
WITH target AS (
    SELECT o.user_id, op.product_id FROM orders o JOIN order_products op ON op.order_id = o.order_id
    WHERE o.eval_set = 'train'
)
SELECT COUNT(*), COUNT(s.product_id)
FROM target t LEFT JOIN agg.agg_n11_score s ON s.user_id = t.user_id AND s.product_id = t.product_id

-- predecir_usuario(user_id, k, decay)
SELECT product_id, score_dX AS score, n_compras, ultima_compra_k
FROM agg_n11_score WHERE user_id = ?
ORDER BY score_dX DESC, ultima_compra_k DESC, product_id ASC LIMIT ?
```

**`predecir_usuario(1, k=5, decay=1.0)`:** producto 196 (score=0.169,
frecuencia=10, recencia=10); producto 12427 (score=0.169, frecuencia=10,
recencia=10); producto 10258 (score=0.153, frecuencia=9, recencia=10).

**Valores clave:** usuarios evaluados=131,209; DECAY elegido=0.9; techo alcanzable=59.9%; hit_rate@10(evaluación)=86.0%; supera a ambos baselines en K=10=True.

### Tabla comparativa (K=10, validación vs. evaluación vs. baselines vs. techo)

| Método | Split | n_usuarios | hit_rate | recall@10 | precision@10 |
|---|---|---:|---:|---:|---:|
| **baseline_decay_recencia** (DECAY=0.9) | Evaluación (80%) | 104,966 | **86.0%** | 34.2% | 29.7% |
| baseline_decay_recencia (DECAY=0.9) | Validación (20%, donde se eligió DECAY) | 26,243 | 86.4% | 34.4% | 29.6% |
| top_k_populares_globales | Evaluación | 104,966 | 45.8% | 7.0% | 7.3% |
| repetir_ultimo_pedido_prior | Evaluación | 104,966 | 74.3% | 24.4% | 30.5% |
| **TECHO** (producto ya en historial prior) | Todo el universo | 131,209 | — | **59.9%** de líneas objetivo alcanzables | — |

**Selección de DECAY en validación (K_SELECCION=10, sin mirar evaluación):**
hit_rate@10 por DECAY: `{1.0: 0.858, 0.9: 0.864, 0.8: 0.863, 0.7: 0.858}` →
elegido **DECAY=0.9** (mejor en validación por un margen pequeño sobre
0.8; 1.0 y 0.7 quedan últimos).

**Tabla completa K=5/10/20** (`res.tablas["metricas"]`, 12 filas, sin truncar):

| método | k | hit_rate | recall@k | precision@k |
|---|---:|---:|---:|---:|
| baseline_decay_recencia (eval) | 5 | 79.2% | 24.1% | 37.4% |
| baseline_decay_recencia (eval) | 10 | 86.0% | 34.2% | 29.7% |
| baseline_decay_recencia (eval) | 20 | 90.3% | 44.9% | 22.5% |
| top_k_populares (eval) | 5 | 36.2% | 4.7% | 9.5% |
| top_k_populares (eval) | 10 | 45.8% | 7.0% | 7.3% |
| top_k_populares (eval) | 20 | 52.2% | 9.5% | 5.1% |
| repetir_ultimo (eval) | 5 | 68.4% | 18.7% | 33.5% |
| repetir_ultimo (eval) | 10 | 74.3% | 24.4% | 30.5% |
| repetir_ultimo (eval) | 20 | 75.8% | 27.6% | 28.9% |

**Hallazgos:** el baseline principal supera a ambos baselines de contexto
en K=10 (86.0% vs. 45.8% populares y 74.3% repetir-último); la métrica en
VALIDACIÓN se ve ligeramente mejor que en EVALUACIÓN (efecto de haber
elegido DECAY ahí), pero el efecto es pequeño (86.4% vs 86.0%) porque es un
baseline determinista sin parámetros ajustados por gradiente. El TECHO
(59.9%) marca que ~40% de las líneas objetivo son productos nuevos,
inalcanzables por cualquier baseline basado solo en historial.

**Invariantes:** 131,209 usuarios evaluados (104,966 evaluación + 26,243
validación); suma de líneas objetivo (`orders JOIN order_products WHERE
eval_set='train'`) = 1,384,617. Verificados en
`test_n11_usuarios_y_lineas_objetivo`.

**Rendimiento:** frío (subprocess, primera ejecución) 4.4-8.1s (misma
variabilidad de caché del SO que N10 — ver "Riesgos"); caliente (mismo
proceso, corridas repetidas) 4.38-4.45s, consistentemente bajo 5s. RSS pico
~609MB. Precomputado en `agregados.py` (`agg_n11_score`/`agg_n11_rank`,
9.3s de construcción multi-hilo) porque rankear 8.47M filas con
`ROW_NUMBER()` 4 veces (una por DECAY) en vivo medía ~15.2s.

---

## Justificación medida de cada parámetro nuevo (resumen)

| Necesidad | Parámetro | Valor | Medición |
|---|---|---|---|
| N7 | MIN_USUARIOS_K | 100 | usuarios con >=k pedidos: 206,209 (k=1) → 867 (k=100, mínimo del dataset). No descarta ningún k en la práctica. |
| N8 | W | 5 | usuarios con >=W pedidos: W=3→100.0%, W=5→84.9% (175,072), W=10→52.1%, W=20→25.6%. |
| N8 | percentil_rutinario/explorador | 0.25/0.75 | cuartiles estándar de índice_novedad. |
| N9 | TAM_MAX | 35 (P99) | percentil 99 medido en vivo sobre 3,346,083 pedidos. |
| N9 | MIN_PEDIDOS | 5 | mismo criterio que N8-W; cubre 175,072 usuarios para desviación intra-usuario. |
| N10 | MIN_TAM | 2 | mínimo por definición (pos_norm indefinido en pedidos de 1); cobertura 95.1% pedidos / 99.5% líneas. |
| N10 | MIN_APARICIONES/MIN_USUARIOS | 50/10 | mismo criterio que N1/N5. |
| N10 | percentil_tasa_baja | 0.25 | cuartil inferior estándar. |
| N11 | DECAY candidatos | 1.0/0.9/0.8/0.7 | rango convencional; elegido por hit_rate@10 en validación (0.9 gana por margen pequeño). |
| C2 (N5) | MIN_USUARIOS_ANCLA | 10 | usuarios_primero entre 42,188 productos "primero alguna vez": P25=2, mediana=6, P75=22. |

---

## Corrida completa: `python -m src.analytics --todas`

```
N1: data/referencia/N1.json (0.56s)
N10: data/referencia/N10.json (6.10s)
N11: data/referencia/N11.json (5.26s)
N2: data/referencia/N2.json (0.17s)
N3: data/referencia/N3.json (0.07s)
N4: data/referencia/N4.json (0.90s)
N5: data/referencia/N5.json (3.88s)
N6: data/referencia/N6.json (1.82s)
N7: data/referencia/N7.json (0.07s)
N8: data/referencia/N8.json (0.43s)
N9: data/referencia/N9.json (0.97s)
```

Medido con `/usr/bin/time -v`: **tiempo total (wall) = 21.41s**, **RSS
pico = 726,248 KB (~709 MB, ~0.71 GB)** — muy por debajo del presupuesto de
2.5GB. N10 (6.10s) y N11 (5.26s) superan el objetivo individual de 5s en
esta corrida secuencial (ver "Riesgos"); las otras 9 corren cómodamente por
debajo.

---

## Fragmento de test con esperado a mano

De `tests/test_analytics_necesidades_b.py` (comparación pareada N7,
fixture con derivación documentada en el docstring del archivo):

```python
def test_n7_pareado_usuario1(dbs):
    con, agg = dbs
    res = n7_evolucion.calcular(con, agg, min_usuarios_k=1)

    # user1: pedido #1 tamaño=3 (productos 1,2,3, todos reordered=0,
    # departamentos {1,2}=2 distintos); pedido #10 tamaño=2 (producto 1
    # reordered=1, producto 4 reordered=0, departamentos {1,2}=2 distintos).
    #   diff_tamano = 2-3 = -1
    #   diff_tasa_recompra = (1/2) - (0/3) = 0.5
    #   diff_productos_nuevos = 1 - 3 = -2  (líneas reordered=0 por pedido)
    #   diff_departamentos_distintos = 2-2 = 0
    # Solo 1 usuario tiene ambos pedidos -> STDDEV_SAMP indefinido (None).
    fila = res.tablas["pareado_1_vs_10"][0]
    assert fila["n_usuarios"] == 1
    assert fila["diff_tamano_media"] == pytest.approx(-1.0)
    assert fila["diff_tamano_std"] is None
    assert fila["diff_tasa_recompra_media"] == pytest.approx(0.5)
    assert fila["diff_productos_nuevos_media"] == pytest.approx(-2.0)
    assert fila["diff_departamentos_distintos_media"] == pytest.approx(0.0)
```

---

## Desviaciones (con motivo)

1. **Bug encontrado y corregido durante esta fase (no pedido explícitamente,
   pero exigido por "cero cifras hardcodeadas"):** `JUSTIF_W` en
   `n8_rutinarios.py` citaba "197,523 de 206,209 (95.8%)" para W=5; la
   medición real contra el dataset completo da 175,072 (84.9%) — el mismo
   número que produce `calcular()` en vivo. Corregido con la distribución
   real (W=3/5/10/20) antes de cerrar la fase; no afectó ningún test
   (el texto es descriptivo, no se usaba en ninguna aserción), pero sí
   violaba la honestidad de las cifras citadas en la justificación.
2. **Paso 0b no encontró un bug de código**, solo un error de transcripción
   en `docs/FASE2A_REPORTE.md` (literales 272.0/0.7214/0.4907/0.2986 vs.
   915.0/0.5783/0.3388/0.6875): se corrigió el texto del reporte de 2A con
   una nota explícita en vez de reescribir el bloque, para dejar rastro de
   qué pasó.
3. **`tests/test_analytics_necesidades_b.py::test_n10_pedido_tamano_1_excluido`**
   tenía un comentario de derivación a mano incorrecto ("order201 es el
   único pedido de tamaño 1"): la fixture en realidad tiene 4 pedidos de
   tamaño 1 (201, 301, 302, 303), no 1. Se corrigió el comentario y el
   valor esperado (4 en vez de 1) tras verificar la fixture fila por fila;
   el código de `n10_impulso.py` estaba bien, era el test el que tenía la
   derivación mal hecha.
4. **C5** no marcó ningún test adicional con `@pytest.mark.integration`
   porque los 6 (ahora 7) ya heredan la marca de `pytestmark` a nivel de
   módulo — se documentó el mecanismo en vez de añadir una marca redundante.
5. Se modificaron `tests/test_analytics_contrato.py`,
   `tests/test_analytics_determinismo.py`, `tests/test_analytics_honestidad.py`,
   `tests/test_analytics_integration.py`, `tests/test_analytics_necesidades.py`
   y `tests/helpers_analytics.py` de Fase 2A: todos por C1/C2/C4/C5 (nuevas
   firmas de `n5_ancla.calcular()`/`n4_frecuencia` con parámetros nuevos,
   `NECESITA_AGREGADO` en vez de tupla hardcodeada `("N1","N2")`,
   `huella_contenido` en las comparaciones de determinismo). Ningún test
   de 2A se eliminó; los que cambiaron de valor esperado (por C1/C2) se
   actualizaron con la misma metodología de derivación a mano.

---

## Riesgos

- **N10 y N11 cruzan el objetivo de 5s en el peor caso medido** (N10: hasta
  6.10s en la corrida `--todas`; N11: hasta 8.1s en frío tras uso intensivo
  de disco). En caliente (mismo proceso, sin presión de caché) ambos
  quedan cómodamente bajo 5s (N10: 2.8s; N11: 4.4s). La causa parece ser
  el mismo patrón de degradación por presión de memoria/caché bajo
  `threads=1`+4GB que motivó precomputar N7 en `agregados.py` durante esta
  misma fase — N10 en particular hace un join en vivo contra las 33.8M
  líneas de `order_products` (no solo el agregado) para la tabla
  tramo×departamento, que es la parte cara. No se precomputó ese join
  completo en esta fase por presupuesto de tiempo; queda como candidato a
  optimizar si el objetivo de 5s se vuelve estricto en producción.
- **`docs/referencia/N*.json` generados por `--todas`** no están
  versionados (mismo patrón que `data/*.duckdb`, ignorados por `.gitignore`);
  si se necesitan como evidencia congelada habría que decidir si se
  versionan o se regeneran bajo demanda.
- **N11 depende de la semántica de `user_id % 5` para el split**: si en
  algún momento se re-numeran los `user_id` (poco probable en este
  dataset estático, pero posible si se combina con otra fuente), el split
  de validación/evaluación cambiaría sin previo aviso. No hay mitigación
  implementada; se documenta como supuesto.

---

## git status (antes del commit final)

```
 M docs/FASE2A_REPORTE.md
 M src/analytics/agregados.py
 M src/analytics/contrato.py
 M src/analytics/n4_frecuencia.py
 M src/analytics/n5_ancla.py
 M src/analytics/n8_rutinarios.py
 M src/analytics/registro.py
 M tests/helpers_analytics.py
 M tests/test_analytics_contrato.py
 M tests/test_analytics_determinismo.py
 M tests/test_analytics_honestidad.py
 M tests/test_analytics_integration.py
 M tests/test_analytics_necesidades.py
?? docs/FASE2B_REPORTE.md
?? src/analytics/n10_impulso.py
?? src/analytics/n11_prediccion.py
?? src/analytics/n7_evolucion.py
?? src/analytics/n9_tamano.py
?? tests/fixtures/instacart_needs_b/
?? tests/test_analytics_cruzada.py
?? tests/test_analytics_integration_b.py
?? tests/test_analytics_necesidades_b.py
```

(`docs/NECESIDADES.md` también modificado — no aparecía en el `git status`
original pegado al inicio de este reporte porque ya estaba comiteado en
`e383f1e`; los cambios de N4/N5/N7-N11 en ese archivo se hicieron durante
esta fase.)

## `pytest -q`

```
........................................................................ [ 36%]
........................................................................ [ 72%]
.......................................................                  [100%]
199 passed in 60.34s (0:01:00)
```

136 previos de 2A + 63 nuevos/actualizados de 2B (fixture `instacart_needs_b`,
`test_analytics_cruzada.py`, `test_analytics_integration_b.py`,
`test_analytics_necesidades_b.py`, y extensiones de contrato/determinismo/
honestidad/integración a N7-N11).

## `pytest -m integration -q`

```
..............                                                           [100%]
14 passed, 185 deselected in 42.27s
```

7 de `test_analytics_integration.py` (N1-N6 + refuerzo C5) + 2 de
`test_analytics_cruzada.py` (C3) + 5 de `test_analytics_integration_b.py`
(invariantes N7-N11) = 14, todos contra `data/instacart.duckdb` +
`data/instacart_agg.duckdb` reales.
