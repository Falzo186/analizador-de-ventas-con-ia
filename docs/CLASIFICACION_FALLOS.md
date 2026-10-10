# Clasificación de fallos

## Fase 12 — contexto de esquema: `product_name` desde `order_products`

- **Fallo:** "qué productos se venden más" generaba `SELECT product_name FROM order_products GROUP BY product_name` → Binder Error (`product_name` solo existe en `products`). Tipo: columna en tabla equivocada / falta de JOIN.
- **Fix:** regla literal en `REGLAS_DE_NEGOCIO` (`src/llm/esquema_instacart.py`) con el SQL completo `order_products op JOIN products p ON op.product_id = p.product_id` + la prohibición explícita, y las claves de JOIN a `aisles`/`departments`. El esquema completo (tabla, columna, tipo vía `PRAGMA table_info` de todas las tablas/vistas reales, por introspección) ya se enviaba desde la Fase 5; se mantiene sin duplicarlo (prompt de sistema: ~6.4k caracteres, cabe en el contexto de 4096 tokens). Test: `test_regla_product_name_obliga_join_con_products`.
- **Verificado en vivo (qwen2.5-coder:1.5b):** "qué productos se venden más", "5 productos menos vendidos" y "departamentos con más productos pedidos" generan el JOIN correcto y ejecutan sin error.
- **Fallo (cont.):** `WHERE op.eval_set = 'train'` → Binder Error (eval_set solo está en `orders`), y "¿qué productos se recompran de forma consistente y cuáles se prueban y se abandonan?" sin respuesta (el modelo no sabía que `reordered` = recompra). Tipo: columna asumida / semántica de columna desconocida.
- **Fix (cont.):** `describir_columnas()` agrega un DESCRIBE compacto (tabla: columna TIPO, sin filas de ejemplo) de todas las tablas/vistas, sumado al esquema existente; reglas explícitas "eval_set NO existe en order_products… verifica contra el esquema dado" (con el JOIN a `orders`) y tasa de recompra (`SUM(reordered=1)*1.0/COUNT(*)`, `HAVING COUNT(*) >= 50`) con SQL literal. Prompt de sistema: ~8.4k caracteres. Verificado en vivo con 1.5b: ambas preguntas generan SQL válido (eval_set vía subconsulta a `orders`).
- **Fallo (cont.):** "qué productos se compran una vez y no se vuelven a comprar" → `WHERE op.reordered = 0 ... ORDER BY COUNT(*) DESC` (salían los más populares, p. ej. Banana: conteo absoluto, no relativo al total). **Fix:** regla + SQL literal que agrupa por producto y exige `recompras = 0` con `HAVING COUNT(*) >= 50`, más la advertencia "NUNCA ordenes por COUNT(*) WHERE reordered=0 solo". En vivo: 8 productos (Vicks DayQuil… 95/0, Organic Castor Oil… 72/0, …). Efecto colateral: la pregunta mixta "consistentes y abandonados" ahora usa esta plantilla y solo responde la mitad "abandonados".
- **Fallo (cont.):** `JOIN order_products op ON a.aisle_id = op.aisle_id` → Binder Error (aisle_id/department_id solo están en `products`). **Fix:** regla "aisles y departments SOLO se pueden unir a través de products…" con el JOIN incorrecto como ejemplo MAL y la cadena aisles → products → order_products como ejemplo BIEN. En vivo, "¿cuál es el pasillo con más productos distintos?" genera la cadena correcta (top: `missing` 1258, el pasillo comodín de Instacart; luego candy chocolate 1246).

## Fase 23 — panel detallado de iteraciones, intento perdido y autocorrección encadenada

> Numeración: en el código, el mecanismo de iteraciones con juez se llama «Fase 11»
> (`src/llm/iteraciones_juez.py`, `tests/test_fase11_iteraciones.py`); es el que en la revisión
> se llamó «Fase 22». Esta sección lo extiende. Evidencia completa: `docs/evidencia/fase23/`.

### 1. Bug: el último intento se perdía

- **Síntoma (revisión):** se veía iterar, pero el último intento no aparecía en el panel.
- **Causa:** `render_iteraciones` guardaba la corrida en `st.session_state` **solo después** de que
  `correr_iteraciones` terminara las N×modelos iteraciones. Durante el último intento la barra decía
  «N−1/N» (el callback solo se llamaba al terminar cada intento) y no había nada en pantalla; si la
  ejecución se cortaba en ese momento —cualquier clic hace que Streamlit relance el script y aborte el
  anterior (`StopException`/`RerunException`, que heredan de `BaseException`)—, la corrida en memoria
  se descartaba y el panel mostraba la corrida anterior o nada. Con el 14b en CPU (~13 min por
  intento) la ventana es enorme. El CLI tenía el mismo defecto: el JSON solo se escribía al final; la
  evidencia de la Fase 11 lo muestra (`docs/evidencia/fase11/corrida_N3.txt` termina en `[6/10]`).
  El conteo en sí (`range(1, n + 1)`) era correcto: no había un off-by-one, sino que lo ya hecho no
  se persistía.
- **Fix:**
  - `ij.nueva_corrida()` crea la corrida (estado `"en curso"`) y la UI la guarda en sesión **antes**
    de empezar; `correr_iteraciones(corrida=...)` agrega cada intento apenas termina y marca
    `"completa"` solo al final. Nada del pipeline atrapa la interrupción de Streamlit, así que lo
    terminado queda.
  - Mientras corre, la barra dice «Corriendo `modelo` · intento k de N (h de T terminados)…»
    (`al_iniciar`) y debajo se dibujan en vivo las tarjetas de los intentos terminados.
  - Si una corrida quedó «en curso», el panel avisa: «⏸️ Corrida interrumpida: se registraron k de T
    intentos» y muestra todos los registrados.
  - CLI: con `--out` reescribe `Nx_iteraciones_juez.json` y `.txt` después de **cada** intento.
- **Tests (`tests/test_fase23_iteraciones.py`):** en el código no existe `ITERACIONES_POR_PREGUNTA`;
  N es `n` (default `ITERACIONES_DEFAULT = 5`, configurable en ⚙️). El test
  `test_con_n_iteraciones_se_registran_exactamente_n_por_modelo` cubre n ∈ {1, 2, 3, 5} × 1 y 2
  modelos × ambos modos: exactamente N por modelo, numerados 1..N, el último es «Intento N de N», y
  el JSON tiene N×modelos. Además: `test_interrupcion_conserva_los_intentos_terminados`,
  `test_cli_escribe_el_log_despues_de_cada_intento`, `test_panel_muestra_todos_los_intentos_incluido_el_ultimo`
  y `test_panel_avisa_corrida_interrumpida_y_muestra_lo_registrado`.

### 2. Panel por necesidad, intento por intento

En el dashboard, «🧪 Iteraciones con juez IA» de la necesidad seleccionada (cualquiera de las 11):

1. **Pregunta** y **Respuesta de referencia (correcta)**: hallazgos y valores clave de
   `data/referencia/{nid}.json` (lo mismo que ve el juez). Antes estaba escondida en la última pestaña.
2. Resumen por modelo: % correctas y la **secuencia** de veredictos (`1❌ → 2🟡 → 3🟡 → 4🟡`).
3. Una tarjeta por intento, con título **«Intento k de N · `modelo` → Veredicto»**, y cada bloque
   rotulado otra vez con «(intento k de N)»:
   - en autocorrección, el **contexto exacto que recibió** del intento k−1;
   - **SQL generado**; si hubo reintento, también el primer SQL rechazado y el motivo;
   - **Resultado crudo** de la consulta (tabla; se guardan hasta 50 filas y el total);
   - **Redacción final** y su origen, con el mismo criterio que el chat: 🤖 modelo / 🤖 segunda
     redacción / 🧩 plantilla. Si no fue del modelo: el aviso 🔢 de cifras alteradas y la **primera
     redacción descartada**, tal cual;
   - **Juez**: veredicto y justificación.

**El caso «¿de dónde salió el 121?».** Antes, cuando el modelo escribía una cifra que no estaba en
el resultado, `finalizar_redaccion` descartaba esa redacción y solo quedaba la final (segunda
redacción o plantilla, con otra cifra). Una cifra podía aparecer y desaparecer sin rastro. Ahora
`ResultadoInstacart` guarda `primera_redaccion`, `segunda_redaccion` y `sql_rechazado`, y cada
`Iteracion` los conserva con su etiqueta. Ejemplo real (N9, intento 1 de 4, 1.5b): la primera
redacción decía «un número promedio de 14,9445 pedidos»; la cifra no está en el resultado y fue
rechazada; la respuesta final es de plantilla (`Banana, n: 491,291; …`). Las tres cosas se ven en la
misma tarjeta. Además, «📋 Entrega» ahora aclara que su JSON es **una corrida aparte** a temperatura
0.0 y no ninguno de los intentos.

### 3. Export del log (para analizarlo después con un modelo más grande)

- Por necesidad: `⬇️ Nx_iteraciones_juez.txt (log legible)` y `⬇️ Descargar Nx_iteraciones_juez.json`.
- De todas las corridas de la sesión: `⬇️ todas_iteraciones_juez.txt` / `.json`.
- CLI: `python -m src.llm.iteraciones_juez N3 N6 … | todas --out <carpeta>`.
- El `.txt` (`ij.a_texto`) no resume nada: pregunta, modo, referencia, resumen y, por intento, el
  contexto recibido, el SQL rechazado/ejecutado, el resultado crudo, las redacciones (con las cifras
  rechazadas), la final y su origen, y el veredicto con su justificación. Está listo para pegarse en
  otra conversación. No hay integración con ningún modelo externo.

### 4. Autocorrección encadenada (`modo_iteracion`)

- **Modos:** `"independiente"` (lo de la Fase 11, sin cambios: N intentos desde cero, mide
  estabilidad/variabilidad) y `"autocorreccion"` (**default**). Se eligen en ⚙️ «Modo de iteración» o
  con `--modo`.
- **Qué cambia lo que se mide:** en autocorrección las iteraciones **ya no son N muestras
  independientes**: cada intento parte del anterior. Lo que se mide es si el modelo **converge** a la
  respuesta correcta cuando se le dice en qué falló. El % de correctas de un modo y del otro no son
  comparables como la misma métrica.
- **Mecanismo:** desde el intento 2, `texto_retroalimentacion(intento_anterior)` (del **mismo
  modelo**: el grande no recibe la cadena del chico) incluye el SQL rechazado y su motivo si hubo,
  el SQL ejecutado, el error o las primeras 10 filas del resultado, la respuesta redactada y el
  veredicto con su justificación. Se agrega al mensaje de usuario de la llamada de SQL (y su
  reintento) y de la primera redacción (`prompts_instacart._con_retroalimentacion`). El reintento
  estricto de cifras no lo lleva. Sin retroalimentación, los prompts quedan idénticos a antes (test
  `test_sin_retroalimentacion_los_prompts_no_cambian`): el chat no cambia.
- **Advertencia de método:** la justificación del juez puede citar cifras de la referencia (p. ej.
  «no proporciona … el tamaño medio, mediana, y P99»). Pasarla al siguiente intento le filtra parte de
  la respuesta correcta: una convergencia puede deberse a eso y no a que el modelo se corrigió.

### 5. Verificación en vivo

#### 5.1 qwen2.5-coder:1.5b, autocorrección, N = 4 (N9 y N6)

Comando: `python -m src.llm.iteraciones_juez N9 N6 --iteraciones 4 --modo autocorreccion --pequeno qwen2.5-coder:1.5b --grande - --timeout 120 --out docs/evidencia/fase23/autocorreccion`.
Salida completa: `docs/evidencia/fase23/autocorreccion/corrida_N9_N6.txt`; logs: `N9_iteraciones_juez.{txt,json}`, `N6_iteraciones_juez.{txt,json}`.

Las dos corridas registraron **4 de 4 intentos**, incluido el último:

```
=== Resumen N9 (completa: 4 de 4 intentos) ===
qwen2.5-coder:1.5b: 0/4 correctas (0%) · 3 parciales · 1 incorrectas · 0 sin veredicto
=== Resumen N6 (completa: 4 de 4 intentos) ===
qwen2.5-coder:1.5b: 0/4 correctas (0%) · 0 parciales · 4 incorrectas · 0 sin veredicto
```

JSON de N9: `estado: completa`, `iteraciones_registradas: 4`, `iteraciones_esperadas: 4`,
etiquetas `['Intento 1 de 4 · qwen2.5-coder:1.5b', …, 'Intento 4 de 4 · qwen2.5-coder:1.5b']`.

**N9: el intento 2 recibió el error pero NO lo corrigió.** Contexto que recibió el intento 2, tal
cual (del `.txt`):

```
INTENTO ANTERIOR DE ESTA MISMA PREGUNTA (intento 1 de 4). Un evaluador lo comparó con la respuesta correcta.
SQL que generaste: SELECT p.product_name, COUNT(*) AS n FROM order_products op JOIN products p ON op.product_id = p.product_id GROUP BY p.product_name ORDER BY n DESC LIMIT 10
Resultado que dio esa consulta:
product_name | n
Banana | 491291
Bag of Organic Bananas | 394930
Organic Strawberries | 275577
Organic Baby Spinach | 251705
Organic Hass Avocado | 220877
Organic Avocado | 184224
Large Lemon | 160792
Strawberries | 149445
Limes | 146660
Organic Whole Milk | 142813
Respuesta que redactaste: Resultado: product_name: Banana, n: 491,291; product_name: Bag of Organic Bananas, n: 394,930; product_name: Organic Strawberries, n: 275,577; product_name: Organic Baby Spinach, n: 251,705; product_name: Organic Hass Avocado, n: 220,877. (5 filas más en la tabla.)
Veredicto del evaluador: INCORRECTA. Motivo: La respuesta generada no proporciona información relevante sobre la distribución del tamaño del pedido y qué factores se asocian con pedidos más grandes o más chicos. En lugar de eso, proporciona una tabla de datos de productos, que no es relevante para la pregunta original.
```

SQL del intento 2: el mismo, sin el `LIMIT 10` (`SELECT p.product_name, COUNT(*) AS n FROM order_products op JOIN products p … GROUP BY p.product_name ORDER BY n DESC`).
Solo cambió la redacción: «…los pedidos más grandes, como los de aguacates… tienen un tamaño promedio de aproximadamente 117,716 unidades…» (segunda redacción; la primera tenía las cifras 6 y 8 rechazadas).
El juez la calificó **PARCIALMENTE CORRECTA** («es correcta en términos de la distribución del tamaño del pedido, pero falta información sobre los factores…»), aunque ninguna cifra coincide con la referencia (media 10.11, mediana 8, P99 35): el juez 1.5b es laxo.
**Intento 3:** mismo SQL, y la respuesta es **copia literal** de la «Respuesta que redactaste» del contexto. Ver su redacción anterior lo ancla en vez de corregirlo. Intento 4: mismo SQL y la misma respuesta con una paráfrasis mínima («…son los que tienen…» → «…tienen…»). Secuencia: `1❌ → 2🟡 → 3🟡 → 4🟡`.

**N6: reacción al error, pero sin converger.**

| Intento | SQL ejecutado (núcleo) | Resultado |
|---|---|---|
| 1 de 4 | `SELECT p.department, a.aisle, COUNT(*) …` | Binder Error: Table "p" does not have a column named "department" |
| 2 de 4 | idéntico al 1 (recibió ese error exacto) | mismo Binder Error |
| 3 de 4 | `SELECT d.department, a.aisle … FROM aisles a JOIN departments d ON a.department_id = d.department_id …` | Binder Error: Table "a" does not have a column named "department_id" |
| 4 de 4 | idéntico al 3 | mismo Binder Error |

El intento 3 es el único cambio **coherente con el error** que recibió: deja de pedir
`p.department` y va a la tabla `departments`. Pero la une por `aisles` en vez de `products`, lo que
la regla de la Fase 12 prohíbe explícitamente. Corrige X y falla por Y, y luego se estanca.

**Conclusión 1.5b:** la retroalimentación llega completa y es visible en cada tarjeta, pero el 1.5b
casi no la usa: en N9 repite el SQL en las 3 transiciones (la 1→2 solo quita el `LIMIT`); en N6 lo
repite en 2 de 3; y llega a copiar su respuesta anterior. 0 de 8 intentos correctos.

#### 5.2 qwen2.5-coder:14b, autocorrección, N = 3 (N6)

<!-- FASE23_14B -->

#### 5.3 Panel real (AppTest contra Ollama y la BD reales)

<!-- FASE23_PANEL -->

### 6. Tests

<!-- FASE23_PYTEST -->

## Fase 24 — 10 necesidades de una sola consulta, retroalimentación sin fuga y pruebas con 8B/7B

> Continúa sobre la Fase 11 (`src/llm/iteraciones_juez.py`) y la Fase 23 (panel por intento, export,
> `modo_iteracion`), sin reconstruirlas. Evidencia completa (ignorada por git, como la de la Fase 23):
> `docs/evidencia/fase24/` — `llama3.1-8b/`, `qwen2.5-coder-7b/` (por necesidad `Nx_iteraciones_juez.{json,txt}`
> y `corrida.txt`) y `panel/panel_llama3.1-8b_N2_N6_N9.txt`.

### 1. Las 10 necesidades nuevas (reemplazo total de las 11)

**Por qué.** Las 11 necesidades anteriores mezclaban varias preguntas en una («y», «vs.»: recompra *y*
abandono, pares frecuentes *y* de asociación fuerte, distribución del tamaño *y* sus factores…). Para
responderlas el modelo tenía que hacer varias consultas internas y combinarlas. Se reemplazaron por 10
preguntas de **una sola consulta** cada una, para enfocar el esfuerzo en que el modelo aprenda a corregirse
con feedback específico. Las preguntas compuestas quedan para una fase futura.

**Decisión de alcance (consultada).** En el código no existía `REFERENCIAS_SQL`: N1-N11 eran los módulos
deterministas `src/analytics/n1_recompra.py` … `n11_prediccion.py` (+ el agregado `data/instacart_agg.duckdb`),
de los que dependían el dashboard, el PDF, la entrega de la Fase 10 y el juez. Se preguntó si reemplazar solo en
el juez o en todo el proyecto; la respuesta fue **reemplazo total**. Ninguna necesidad vieja se conservó en
paralelo; su definición queda en el historial de git y en `docs/FASE2A_REPORTE.md`/`FASE2B_REPORTE.md`.

**Qué se borró:** `src/analytics/n1_recompra.py` … `n11_prediccion.py`, `src/analytics/agregados.py`, sus
tests (`test_analytics_agregados`, `_cruzada`, `_integration`, `_integration_b`, `_necesidades`,
`_necesidades_b`) y la fixture `tests/fixtures/instacart_needs_b/`. El parámetro `agg_path`/`--agg` desapareció
de `servicio`, el PDF y los CLI. `data/instacart_agg.duckdb` ya no se usa (no se borró del disco: es un archivo
de datos del usuario; se puede eliminar).

**Dónde quedó cada cosa:**

| Qué | Dónde |
|---|---|
| Definición de las 10 (pregunta, decisión, SQL, forma de respuesta, redacción) | `src/analytics/necesidades.py` (`DEFINICIONES`, `DefNecesidad`, `calcular`) |
| Registro id → `calcular(con)` | `src/analytics/registro.py` (`NECESIDADES`, `definicion`, `invocar`) |
| SQL + resultado crudo + redacción (lo que ve el juez) | `data/referencia/Nx.json`, mismo contrato `ResultadoNecesidad`: `consultas` (una sola, `"respuesta"`), `tablas["respuesta"]`, `hallazgos` (redacción generada por código, sin IA), `valores_clave` |
| Definiciones narradas | `docs/NECESIDADES.md` (reescrito) |
| Entrega del curso | ahora las 10 (`NECESIDADES_ENTREGA = tuple(NECESIDADES)`); `docs/ENTREGA_5_NECESIDADES.md` → `docs/ENTREGA_NECESIDADES.md` |
| Gráfica | una genérica (barras nombre vs. métrica) en `src/dashboard/graficas_datos.py::datos_grafica`, usada por el dashboard (Altair) y el PDF (matplotlib); las escalares no tienen gráfica |

Los IDs siguen siendo **N1-N10** (con el significado nuevo), para no tocar `data/referencia/{nid}.json`, el
panel, el export ni los CLI. **Riesgo que esto creaba:** un `N1.json` viejo tiene la misma `huella_bd` que la BD
actual y se habría servido como el N1 nuevo. **Fix:** `servicio._cache_vigente` ahora exige además que
`pregunta` y SQL del JSON coincidan con la definición actual (test
`test_json_de_otra_definicion_se_recalcula_aunque_la_huella_coincida`).

**SQL y resultados reales** (`python -m src.analytics --todas`, DuckDB 1.5.5, `threads=1`):

| # | Pregunta | SQL de referencia (núcleo) | Resultado | Tiempo |
|---|---|---|---|---|
| N1 | 10 productos más vendidos por unidades | `COUNT(*)` de `order_products` ⨝ `products` por `product_name`, `LIMIT 10` | Banana 491,291; Bag of Organic Bananas 394,930; Organic Strawberries 275,577; … Organic Whole Milk 142,813 | 1.96 s |
| N2 | Departamento con más pedidos | `COUNT(DISTINCT op.order_id)` por `department` (op ⨝ products ⨝ departments) | produce, 2,506,247 pedidos distintos (dairy eggs 2,264,738) | 5.00 s |
| N3 | ≥ 50 compras, tasa de recompra más alta | `SUM(reordered=1)*1.0/COUNT(*)`, `HAVING COUNT(*) >= 50`, desc | Raw Veggie Wrappers 65/69 = 94.2%; Serenity Ultimate Extrema Overnight Pads 93.3%; Chocolate Love Bar 92.2% … | 1.79 s |
| N4 | ≥ 50 compras, tasa más baja | igual, asc | 8 productos con tasa 0 (Vicks DayQuil and NyQuil Cold & Flu Relief Combo 0/95 … Gel Food Colors 0/50), luego Ground Sage 1/214 | 1.74 s |
| N5 | Tamaño promedio del carrito | `AVG` de líneas por `order_id` | 10.107 (33,819,106 / 3,346,083) | 1.15 s |
| N6 | Cada cuántos días vuelve un cliente | `AVG(days_since_prior_order)` en `orders` | 11.115 días (3,214,874 pedidos con intervalo) | 0.03 s |
| N7 | Pasillo con más productos distintos a la venta | `COUNT(*)` de `products` por `aisle` | `missing` 1,258 (comodín); primer pasillo real: candy chocolate 1,246 | 0.00 s |
| N8 | Pasillo con más unidades vendidas | `COUNT(*)` de `order_products` por `aisle` | fresh fruits 3,792,661 (fresh vegetables 3,568,630) | 0.89 s |
| N9 | Producto que más veces va primero al carrito | `COUNT(*)` con `add_to_cart_order = 1` | Banana 115,521 (Bag of Organic Bananas 82,877) | 0.41 s |
| N10 | % de líneas recompradas | `SUM(reordered=1)*100.0/COUNT(*)` | 59.006% (19,955,360 / 33,819,106) | 0.12 s |

**Decisiones propias de definición** (también en `limitaciones` de cada JSON y en `docs/NECESIDADES.md`):
- «Unidades» (N1, N8) = líneas de pedido: el dataset no tiene cantidad.
- «Pedidos» de un departamento (N2) = pedidos **distintos** con al menos un producto de él. Contar líneas
  también da produce, pero con 9,888,378: es otra métrica.
- N6 incluye los pedidos `test` (tienen intervalo aunque no tengan productos; sin ellos daría 10.97) y es
  promedio por pedido, no por cliente. El tope en 30 se declara como limitación crítica.
- N7 «a la venta» = catálogo (`products`). El primer lugar es el comodín `missing`; la redacción nombra también
  el primer pasillo real.
- Forma de la respuesta (`tipo`): `lista` (N1: 10 filas, fijado por la pregunta; N3/N4: la pregunta no fija
  cuántas, `filas_esperadas=None`, la referencia guarda 10 por convención), `unico` (respuesta = primera fila;
  se guarda el top 10 como contexto: N2, N7, N8, N9) y `escalar` (N5, N6, N10).
- Orden de columnas: el JSON se serializa con `sort_keys` (determinismo de la Fase 2B), así que el orden del
  SELECT se pierde al leer de caché. `necesidades.columnas_ordenadas` fija nombre, métrica y el resto
  alfabético; sin eso, el PDF salía distinto recién calculado y desde caché (lo detectó
  `test_determinismo_de_contenido_dos_corridas`).

### 2. Feedback específico entre intentos, sin fuga de la respuesta

Nuevo módulo `src/llm/retroalimentacion.py`; cambios en `prompts_instacart.prompt_juez_instacart` e
`iteraciones_juez` (`juzgar`, `texto_retroalimentacion`, `correr_iteracion`).

**Juez: diagnóstico del enfoque separado de la respuesta correcta.** El juez ahora ve el SQL y el resultado del
intento (antes solo la redacción) y la referencia completa: SQL de referencia, resultado crudo, redacción y
valores clave. Devuelve JSON con tres claves, en este orden (primero compara y después diagnostica):
- `comparacion`: en qué coincide o difiere con la referencia; **puede citar valores**. Se guarda en
  `Iteracion.justificacion`, se ve en el panel y en el log, y **nunca** se pasa al modelo.
- `veredicto`.
- `diagnostico_enfoque`: qué está mal **del enfoque** (JOIN que falta, columna, agrupación, métrica, orden,
  LIMIT, redacción). El prompt prohíbe cifras, nombres o valores de la referencia y copiar su SQL. Es lo único
  del juez que pasa al siguiente intento, y pasa **saneado**.

**Saneo (`rf.sanear`).** Se quita cada **oración** del diagnóstico que contenga un valor de la referencia (todas
las celdas de su resultado). No se enmascara con «[oculto]»: enmascarar diría dónde estaba el dato correcto. Si
no queda ninguna oración, pasa un aviso fijo sin valores (`DIAGNOSTICO_OMITIDO`). Las reglas de detección
(`rf.fugas`) son decisión de esta fase:
- se ignoran las cifras que aparecen en la pregunta (10, 50, 1);
- enteros de la referencia con |v| ≥ 10, en cualquier formato (491291, 491,291, 491.291); los enteros chicos
  (0, 1, 2 recompras) son demasiado genéricos para considerarlos fuga;
- no enteros (0.942, 10.107, 59.006): v y v×100 redondeados a los decimales del token (0.94, 94.2, 10.11); con 0
  decimales, solo si el redondeo es ≥ 10 (94, 10, 59);
- textos (nombres de producto, pasillo, departamento) de 3 caracteres o más, como palabra completa.

**Qué recibe ahora el intento k+1** (`texto_retroalimentacion`):
- el SQL que generó, y el rechazado con su motivo si hubo reintento;
- si falló: el error **tal cual** (`Error (mensaje exacto): …`). El de DuckDB llega completo, con
  `Candidate bindings` y `LINE 1:`; verificado en vivo y en
  `test_error_de_duckdb_pasa_completo_con_candidate_bindings`;
- si ejecutó: la **forma** del resultado (cuántas filas y qué columnas), no sus valores;
- el **chequeo de forma** determinista (`rf.chequeo_forma`/`chequeo_error`), que se deduce de la pregunta y no de
  la referencia: «la pregunta pide 10 filas y tu consulta devolvió 2», «pide un solo número y devolvió 1,000 o más
  (la guarda corta en 1,000)», «tu resultado no trae ninguna columna de texto: identifica el producto por su
  nombre», «tus filas no están ordenadas por ninguna columna numérica», y ante un timeout «evita subconsultas
  correlacionadas». Las dos últimas se agregaron al ver la primera corrida de prueba de N2 (un timeout por
  `= ANY (subconsulta)` y 21 filas sin `ORDER BY`, ambos sin ninguna pista);
- el veredicto y el diagnóstico saneado.

**Lo que se dejó de pasar, y por qué.** Antes se pasaban las primeras 10 filas del resultado anterior y la
redacción anterior. Ya no:
1. Si el intento anterior ya coincidía con la referencia, repetir sus filas es repetir la respuesta, y
   enmascarar solo las que coinciden le diría al modelo cuáles son las correctas.
2. En la Fase 23 el 1.5b copió literalmente su redacción anterior en vez de corregirse.

Costo observado: en qwen N5-4/5 el resultado del propio modelo ya era 10.107, pero la redacción decía «10». El
diagnóstico del juez («debe decir 10.11») se omitió por fuga, porque 10.11 también es la cifra de la referencia,
y el modelo solo recibió «La respuesta redactada es incorrecta.».

**Chequeo automático vs. referencia (`rf.coincide_con_referencia`)**, solo informativo (panel, log, este
reporte): nunca pasa al modelo. Se agregó para contrastar al juez-LLM:
- escalar: alguna cifra de la primera fila a ±0.5 % (acepta fracción vs. porcentaje);
- lista: con k fijo (N1), ≥ 80 % de los k nombres en las primeras k filas; sin k (N3/N4), ≥ 80 % de los primeros
  m nombres del intento (m ≥ 3) dentro del top 10 de la referencia, así los empates en otro orden cuentan;
- único: el nombre de la primera fila y, **si la fila trae cifras**, alguna igual a la de la referencia.

Esta última regla se relajó **después** de ver la corrida de llama. Exigir nombre **y** cifra marcaba como «no
coincide» los intentos N9-2/4/5, que tienen el SQL correcto pero solo devuelven el nombre; la pregunta pide el
nombre. Efecto secundario, reportado tal cual: compara resultados, no métodos, así que llama N2-5 (cuenta líneas
y no trae la cifra) y llama N9-1 (sin el `WHERE add_to_cart_order = 1`) cuentan como coincidencia porque el
nombre es el correcto. El log de llama y el de qwen guardan el valor con la regla anterior (`coincide_referencia`);
las tablas de abajo lo recalculan con la regla final a partir de las filas guardadas (llama: 34 → 40 de 50; qwen:
36 → 36).

**Test de no-fuga** (`tests/test_fase24_retroalimentacion.py`). `test_retroalimentacion_no_contiene_valores_de_la_referencia`,
×10 necesidades, contra la BD real y las referencias reales del Paso 1. Usa el **peor caso**: el intento 1
ejecuta exactamente el SQL de referencia (su resultado ES la respuesta), su redacción cita la respuesta y el juez
la cita en la comparación y en una de las dos oraciones del diagnóstico. Verifica que lo que recibe el intento 2,
y lo que de verdad llega al prompt de SQL del intento 2, no tiene ningún valor de la referencia. La verificación
**no** usa `rf.fugas` (sería probar la función consigo misma): genera por su cuenta los formatos de cada valor
(491291, 491,291, 491.291, 94.2, 0.94, 94…) y los busca como token. Comprueba además que el feedback sigue siendo
específico (SQL, forma, veredicto, la oración limpia) y que se omitió exactamente 1 oración. Se verificó a mano
que ese detector sí marca la cita en las 10 necesidades.

### 3. Pruebas con 8B y 7B (autocorrección, 10 necesidades × 5 intentos)

```
python -m src.llm.iteraciones_juez todas --iteraciones 5 --modo autocorreccion --pequeno llama3.1:8b --grande - --timeout 180 --out docs/evidencia/fase24/llama3.1-8b
python -m src.llm.iteraciones_juez todas --iteraciones 5 --modo autocorreccion --pequeno qwen2.5-coder:latest --grande - --timeout 180 --out docs/evidencia/fase24/qwen2.5-coder-7b
```

Con temperatura 0.5 (juez 0.0), en una RTX 5060 de 8 GB: llama 5.6 s/intento de media (pipeline + juez), qwen
6.0 s. Cada modelo es su propio juez (diseño de la Fase 11). El 14B no se tocó. `llama3.1:8b` se descargó en esta
fase con autorización; `qwen2.5-coder:latest` (7B) ya estaba en la máquina.

**llama3.1:8b**

| Nec. | Juez por intento | 1.er «correcta» | Chequeo automático | 1.ª coincidencia | Errores de ejecución |
|---|---|---|---|---|---|
| N1 | 1✅ 2✅ 3✅ 4🟡 5🟡 | 1 | 1✓ 2✓ 3✓ 4✓ 5✓ | 1 | 0 |
| N2 | 1🟡 2❌ 3❌ 4❌ 5❌ | — | 1✗ 2✗ 3✗ 4✗ 5✓ | 5 | 3 |
| N3 | 1❌ 2🟡 3❌ 4🟡 5🟡 | — | 1✓ 2✓ 3✓ 4✓ 5✓ | 1 | 0 |
| N4 | 1🟡 2❌ 3❌ 4🟡 5🟡 | — | 1✓ 2✓ 3✓ 4✓ 5✓ | 1 | 0 |
| N5 | 1❌ 2❌ 3🟡 4🟡 5🟡 | — | 1✗ 2✗ 3✓ 4✓ 5✓ | 3 | 1 |
| N6 | 1🟡 2🟡 3🟡 4🟡 5❌ | — | 1✓ 2✓ 3✓ 4✓ 5✗ | 1 | 0 |
| N7 | 1🟡 2🟡 3🟡 4🟡 5🟡 | — | 1✓ 2✓ 3✓ 4✓ 5✓ | 1 | 0 |
| N8 | 1🟡 2🟡 3❌ 4✅ 5❌ | 4 | 1✗ 2✓ 3✗ 4✓ 5✗ | 2 | 0 |
| N9 | 1🟡 2🟡 3🟡 4🟡 5🟡 | — | 1✓ 2✓ 3✓ 4✓ 5✓ | 1 | 0 |
| N10 | 1🟡 2🟡 3🟡 4🟡 5🟡 | — | 1✓ 2✓ 3✓ 4✓ 5✓ | 1 | 0 |

Totales: juez «correcta» 4/50; chequeo automático 40/50. Llegan a «correcta» del juez en algún intento:
**2 de 10** (N1 en el 1, N8 en el 4). Llegan a coincidir con la referencia: **10 de 10** (7 en el intento 1, N8
en el 2, N5 en el 3, N2 en el 5 por nombre). 40 textos de feedback pasados, **0 con fugas**, 1 oración omitida
(N7-1: «Falta considerar que 'missing' es un pasillo comodín…»).

**qwen2.5-coder:latest (7B)**

| Nec. | Juez por intento | 1.er «correcta» | Chequeo automático | 1.ª coincidencia | Errores de ejecución |
|---|---|---|---|---|---|
| N1 | 1✅ 2✅ 3❌ 4✅ 5✅ | 1 | 1✓ 2✓ 3✗ 4✓ 5✓ | 1 | 1 |
| N2 | 1✅ 2🟡 3✅ 4✅ 5✅ | 1 | 1✓ 2✓ 3✓ 4✓ 5✓ | 1 | 0 |
| N3 | 1✅ 2✅ 3✅ 4✅ 5✅ | 1 | 1✓ 2✓ 3✓ 4✓ 5✓ | 1 | 0 |
| N4 | 1🟡 2🟡 3✅ 4🟡 5🟡 | 3 | 1✗ 2✗ 3✓ 4✓ 5✓ | 3 | 0 |
| N5 | 1❌ 2❌ 3❌ 4❌ 5❌ | — | 1✗ 2✗ 3✗ 4✓ 5✓ | 4 | 0 |
| N6 | 1✅ 2✅ 3✅ 4❌ 5❌ | 1 | 1✓ 2✓ 3✓ 4✗ 5✗ | 1 | 0 |
| N7 | 1🟡 2✅ 3✅ 4🟡 5🟡 | 2 | 1✓ 2✓ 3✓ 4✓ 5✓ | 1 | 0 |
| N8 | 1❌ 2❌ 3❌ 4❌ 5❌ | — | 1✗ 2✗ 3✗ 4✗ 5✗ | — | 0 |
| N9 | 1🟡 2✅ 3✅ 4✅ 5✅ | 2 | 1✓ 2✓ 3✓ 4✓ 5✓ | 1 | 0 |
| N10 | 1❌ 2🟡 3❌ 4🟡 5❌ | — | 1✓ 2✓ 3✗ 4✓ 5✓ | 1 | 1 |

Totales: juez «correcta» 23/50; chequeo automático 36/50. Llegan a «correcta» del juez: **7 de 10** (en el
intento 1: N1, N2, N3, N6; en el 2: N7, N9; en el 3: N4). Llegan a coincidir: **9 de 10** (N4 en el 3, N5 en el
4; N8 nunca). 40 textos de feedback, **0 con fugas**, 5 oraciones omitidas (en N4-1/2/4 y N5-4/5 el juez
escribió el producto o la cifra correctos dentro del diagnóstico: el saneo los quitó).

**En qué intento convergen.** Cuando el modelo acierta, casi siempre es en el **intento 1**: llama 7/10 y qwen
7/10 coinciden con la referencia desde el primero. La autocorrección que sí funcionó fue la provocada por
**errores de ejecución** y por el chequeo de forma:
- llama N5: intento 2 `SELECT AVG(COUNT(*)) … GROUP BY order_id` → `Binder Error: aggregate function calls
  cannot be nested`; intento 3 `COUNT(*) / (SELECT COUNT(DISTINCT order_id) …)` = 10.107 ✓.
- qwen N5: intentos 2-3 devolvían un promedio por pedido (1,000 o más filas) con la pista «la pregunta pide un
  solo número y tu consulta devolvió 1,000 o más filas»; intento 4: `AVG(total_productos)` sobre la subconsulta
  por pedido = 10.107 ✓.
- qwen N4: los intentos 1-2 excluían los productos con 0 recompras (`… AND SUM(…) > 0`); el diagnóstico de ambos
  citaba el producto correcto y se omitió entero; el intento 3 quitó el filtro ✓.
- Panel real llama N2 (sección 4): el intento 1 recibió «Falta el JOIN con order_products… contando líneas en
  vez de pedidos distintos»; el intento 2, después de un `Binder Error … Candidate bindings: "order_id"`, ejecutó
  `COUNT(DISTINCT op.order_id)` ⨝ order_products → produce 2,506,247 ✓.

**Casos que NO convergen, o que se salen de la respuesta correcta.** El hallazgo principal de esta fase es que
**el cuello de botella es el juez-LLM, no el saneo**: el feedback llega sin fuga, pero muchas veces es falso.
1. **El juez castiga respuestas correctas.** llama dio «correcta» en 4 de los 40 intentos que coincidían con la
   referencia. Lo que pidió de más: el formato de la redacción («no sigue el formato de lista»), el orden de
   columnas, desempates («Falta el ORDER BY p.product_name»), los totales que trae la referencia (N10: «no
   proporciona la cantidad exacta de líneas recompradas») o las 10 filas de contexto en preguntas de respuesta
   única (N2 panel: «Falta el LIMIT para mostrar los 10 primeros departamentos»). qwen hace lo mismo, pero menos.
2. **El feedback falso saca al modelo de la respuesta correcta.**
   - llama N6: el intento 4 era correcto (11.115); el juez lo dio por «parcial» con «Falta el filtro para incluir
     solo los pedidos con days_since_prior_order distinto de NULL y 30»; el intento 5 obedeció
     (`AND days_since_prior_order != 30`) → 8.66 ✗.
   - llama N8: el intento 2 era correcto (`COUNT(*)`, fresh fruits 3,792,661); el diagnóstico pedía «el GROUP BY y
     ORDER BY para mostrar los 10 primeros»; el intento 3 volvió a `COUNT(DISTINCT op.product_id)` ✗. Oscila
     ✗✓✗✓✗.
   - llama N9 en el panel: el intento 2 era correcto; el diagnóstico decía «Estás agrupando por order_id» (no lo
     hacía); el intento 3 cambió a `GROUP BY p.product_id` → Binder Error.
3. **Sin feedback negativo también hay regresión.** qwen N6: correcto en los intentos 1-3 con veredicto
   «CORRECTA» y diagnóstico vacío; en el 4 agregó por su cuenta `AND eval_set = 'prior'` (10.707 ✗), y el juez lo
   reforzó: «falta el WHERE eval_set = 'prior'». A temperatura 0.5, «sigue igual» no es estable.
4. **No converge aunque recibe feedback correcto.** qwen N8 nunca acierta: alterna entre
   `COUNT(DISTINCT op.product_id)` (missing 1,258) y `SUM(op.add_to_cart_order)` (fresh vegetables 31,665,272).
   En el intento 4 el diagnóstico fue **correcto** («se debe contar … COUNT(*) en lugar de sumar el valor de
   add_to_cart_order») y el intento 5 volvió a `COUNT(DISTINCT op.product_id)`. Los diagnósticos de los intentos
   2 y 3 eran falsos y uno es copia literal del ejemplo del prompt del juez («te quedaste con el conteo absoluto
   en vez de dividir entre el total de veces comprado»): los ejemplos del prompt se repiten como plantilla.
   llama N2 tampoco convergió con un diagnóstico correcto desde el intento 1: encadenó tres `Binder Error`
   distintos (corrige uno, introduce otro) y en el 5 acertó el nombre contando líneas.

**Lectura.** Con preguntas de una consulta, 8B y 7B aciertan el SQL casi siempre al primer intento. La
autocorrección sirve sobre todo con errores de ejecución y con el chequeo de forma, que son deterministas. Con un
juez del mismo tamaño, el diagnóstico es ruido en buena parte de los casos y produce regresiones.
**Recomendaciones para la siguiente fase (no implementadas aquí):**
(a) que el veredicto que se pasa al modelo salga del chequeo automático, o pasarle ese chequeo al juez como dato;
(b) en las necesidades de respuesta única, mostrarle al juez solo la primera fila de la referencia, no el top 10
de contexto;
(c) mostrarle al juez solo lo que la pregunta pide, sin los totales extra;
(d) quitar del prompt del juez los ejemplos literales de diagnóstico, o variarlos;
(e) si el veredicto es «correcta», no volver a pedir el SQL (cortar la cadena).

### 4. Verificación

**pytest** (completo, `python -m pytest -q`):

```
FAILED tests/test_app_instacart.py::test_modelos_cargados_lee_api_ps - Assert...
FAILED tests/test_generar_ficha_dataset.py::test_construir_ficha_coincide_con_dataset_instacart_md
2 failed, 488 passed, 2 xpassed in 131.78s (0:02:11)
```

Las 2 fallas ya estaban **antes** de esta fase (línea base en esta máquina: 4 failed, 459 passed, 1 skipped,
2 xpassed) y son del entorno: `ollama` de esta máquina devuelve `expires_at` como `datetime` en vez de texto, y
la ficha imprime `data\instacart` con separador de Windows. De las otras 2 de la línea base:
`test_ollama_caido_se_informa_sin_romper_la_app` estaba roto desde la Fase 23 (el selector «Modo de iteración»
aparece aunque Ollama esté caído y el test exigía cero selectores). Se corrigió para exigir que no haya
selectores **de modelo**. La de coherencia depende de Ollama en vivo y esta vez pasó.

**Panel real** (`AppTest` de `src/ui/app_instacart.py` contra Ollama y `data/instacart.duckdb`; llama3.1:8b, 3
intentos, autocorrección, esfuerzo Ultra; N2, N6, N9). Volcado completo:
`docs/evidencia/fase24/panel/panel_llama3.1-8b_N2_N6_N9.txt`. Cada tarjeta muestra el contexto recibido, el SQL
(y el rechazado con el mensaje de DuckDB), el resultado crudo, la redacción, el veredicto con la comparación,
el diagnóstico que pasa (y el crudo si se omitió algo) y el chequeo automático. Resumen:

| Nec. | Intento | SQL ejecutado (núcleo) | Resultado | Juez | Chequeo |
|---|---|---|---|---|---|
| N2 | 1 | `COUNT(o.order_id)` con `orders o JOIN products p ON o.order_id = p.product_id` | personal care, 6,563 | ❌ | ✗ |
| N2 | 2 | (1.º rechazado: `Binder Error … Candidate bindings: "order_id"`) `COUNT(DISTINCT op.order_id)` orders ⨝ order_products ⨝ products ⨝ departments, `LIMIT 1` | produce, 2,506,247 | 🟡 «solo muestra un resultado en lugar de los 10 primeros» | ✓ |
| N2 | 3 | igual, sin `LIMIT` | 21 filas, produce 2,506,247 | 🟡 «no se limitó a los 10 primeros» | ✓ |
| N6 | 1 | `AVG(days_since_prior_order) … WHERE eval_set = 'train'` | 17.05 | ❌ | ✗ |
| N6 | 2 | `… WHERE eval_set = 'prior' OR eval_set IS NULL` | 10.707 | ❌ | ✗ |
| N6 | 3 | `… OR eval_set IS NULL AND days_since_prior_order > 0` | 10.707 | ❌ | ✗ |
| N9 | 1 | (1.º rechazado: `must appear in the GROUP BY`) `GROUP BY p.product_name ORDER BY COUNT(*) DESC LIMIT 1`, sin `WHERE` | Banana | 🟡 | ✓ (por nombre; método incompleto) |
| N9 | 2 | `WHERE add_to_cart_order = 1 GROUP BY p.product_name ORDER BY COUNT(op.order_id) DESC LIMIT 1` | Banana | 🟡 «difiere en las veces» | ✓ |
| N9 | 3 | `… GROUP BY p.product_id` | Binder Error (`must appear in the GROUP BY`) | ❌ (automático) | ✗ |

Contexto exacto que recibió el intento 2 de N2 en el panel (sin ningún valor de la referencia):

```
INTENTO ANTERIOR DE ESTA MISMA PREGUNTA (intento 1 de 3). Un evaluador lo comparó con la respuesta correcta, que no se te muestra.
SQL que generaste: SELECT d.department, COUNT(o.order_id) AS n_pedidos FROM orders o JOIN products p ON o.order_id = p.product_id JOIN departments d ON p.department_id = d.department_id GROUP BY d.department ORDER BY n_pedidos DESC LIMIT 1
Tu consulta se ejecutó y devolvió 1 fila(s) con las columnas: department, n_pedidos.
Veredicto del evaluador: INCORRECTA.
Qué está mal en el enfoque, según el evaluador: Falta el JOIN con order_products para traer el conteo de pedidos distintos, y se está contando líneas en vez de pedidos distintos.
```

N6 en el panel repite el patrón de la sección 3: el juez dio «Falta el filtro por todos los conjuntos de
evaluación (eval_set) para incluir también los datos del conjunto de prueba» (el problema real era el filtro
mismo) y el modelo se quedó en 10.707.

### 5. Otras decisiones tomadas en esta fase

- **Entrega del curso:** ahora las 10 necesidades (antes 5 de 11). Si el curso exige exactamente 5, es una
  línea: `servicio.NECESIDADES_ENTREGA`.
- **Dashboard y PDF:** cada panel o sección muestra «Respuesta» (la redacción), la gráfica genérica, los valores
  clave escalares (las listas ya están en la respuesta), el SQL de referencia (el PDF lo tiene en el apéndice) y
  el resultado crudo.
- **Limitaciones críticas del PDF:** las palabras clave que el test exige ahora son las de las nuevas
  necesidades (unidades = líneas, `COUNT(DISTINCT order_id)`, tasa = recompras / veces, censura en 30, pasillo
  `missing`).
- **Resumen por modelo** (`ResumenModelo`, CLI, panel, log): agrega el primer intento «correcta», las
  coincidencias del chequeo automático y la primera coincidencia.
- **No se cambiaron** los defaults de modelos de iteración (`qwen2.5-coder:1.5b` / `qwen2.5-coder:14b`), ni el
  chat, ni la temperatura 0.5. En el CLI, el 14B se excluyó con `--grande -`.
- Los marcadores `<!-- FASE23_14B -->`, `<!-- FASE23_PANEL -->` y `<!-- FASE23_PYTEST -->` de la sección de la
  Fase 23 siguen vacíos: son de esa fase y no se rellenaron aquí.

## Fase 25 — El chequeo automático decide el veredicto; el juez-LLM solo diagnostica

> Continúa sobre la Fase 24. Evidencia (ignorada por git): `docs/evidencia/fase25/{chequeo,juez}-r{1,2,3}/{llama3.1-8b,qwen2.5-coder-7b}/`
> (`Nx_iteraciones_juez.{json,txt}` y `corrida.txt` por corrida). La corrida «juez r1» es la de la Fase 24
> (`docs/evidencia/fase24/`).

### 1. Por qué (evidencia de la Fase 24)

Con el juez-LLM como árbitro (Fase 24, 10 necesidades × 5 intentos):
- llama3.1:8b: el chequeo automático encontró **40/50** intentos que coinciden con la referencia; el juez marcó
  «correcta» solo **4/50**. De los 46 intentos que juzgó, coincidió con el chequeo en 10: **36 falsos negativos**
  (respuesta correcta que no dio por correcta) y **0 falsos positivos**.
- qwen2.5-coder 7B: 36/50 según el chequeo, 23/50 según el juez; 13 falsos negativos, 0 falsos positivos.
- El veredicto falso arrastraba un diagnóstico falso que sacaba al modelo de la respuesta correcta: llama N6
  (correcto en el intento 4 → «falta excluir los 30» → 8.66 en el 5), llama N8 (✓ en el 2 → ✗ en el 3), qwen N6
  (✓✓✓ y luego `eval_set = 'prior'`, reforzado por el juez).

Como el juez nunca dio por correcto algo que estaba mal, pero sí lo contrario muchísimas veces, el veredicto
binario del chequeo automático es estrictamente más confiable para decidir.

### 2. El cambio

**Quién decide (`arbitro`).** Nuevo parámetro de `nueva_corrida`/`correr_iteraciones`/CLI (`--arbitro`):
- `"chequeo"` — **default en modo autocorrección**. Veredicto = `rf.coincide_con_referencia`: coincide →
  «correcta», no → «incorrecta» (binario: el chequeo no tiene «parcial»). `origen_veredicto =
  "chequeo_automatico"`, `justificacion` = el detalle del chequeo (puede citar la referencia; no pasa al modelo).
- `"juez"` — el comportamiento de las Fases 11-24, intacto. Es el default del modo **independiente** (no se
  tocó) y sirve, con `--arbitro juez`, para repetir la Fase 24 con el mismo código.

**El juez-LLM se queda, en dos papeles separados** (con árbitro «chequeo»):
1. **Diagnóstico** (`diagnosticar`, prompt nuevo `prompt_diagnostico_instacart`): **solo** si el chequeo dijo
   que el intento falló. El prompt le dice que un chequeo automático **ya determinó** que es incorrecto, le pasa
   el detalle de qué no coincide, y le pide únicamente la causa (`comparacion` + `diagnostico_enfoque`), **sin
   veredicto**. El diagnóstico se sanea igual que en la Fase 24 (sin valores de la referencia). Si el chequeo dice
   que acertó, no se llama: no hay nada que diagnosticar.
2. **Medición** (`medir_juez`, default activo; `--sin-medir-juez` lo apaga): en cada intento que produjo
   respuesta se le pide su veredicto con el **mismo prompt de la Fase 24**, a ciegas respecto del chequeo, solo
   para seguir midiendo qué tan seguido se equivoca. Se guarda aparte (`juez_veredicto`, `juez_justificacion`,
   `juez_origen`, `juez_crudo`, `duracion_juez_medicion_s`), **no decide nada y no se pasa al modelo**.

Por qué dos llamadas y no una: el paso 1 de la fase pide seguir documentando cuánto se equivoca el juez, y su
error principal es justamente sobre los intentos **correctos** (falsos negativos), que con el diseño del paso 2
ya no se le muestran. Medirlo exige preguntarle también en esos, sin que su respuesta afecte nada.

Si el pipeline no produjo respuesta (error de SQL, timeout), no se llama al juez en ningún papel: el error exacto
de DuckDB sigue siendo la retroalimentación (Fase 24).

**Qué recibe el intento siguiente** (`texto_retroalimentacion`, árbitro «chequeo»): lo mismo que en la Fase 24
(SQL, error exacto o forma del resultado, chequeo de forma), con el veredicto rotulado «Veredicto (chequeo
automático contra la respuesta correcta): …» y:
- si falló: «Qué está mal en el enfoque, según el evaluador: <diagnóstico saneado>»;
- si acertó: «Tu resultado coincide con la respuesta correcta: conserva el enfoque.» y nada más. El detalle del
  chequeo (p. ej. «nombre sí, cifra no») **no** se pasa: diría qué parte del resultado ya era la correcta.

**Panel y log.** La tarjeta de cada intento muestra «Veredicto — chequeo automático contra la referencia» (con
su detalle) y, debajo, «⚖️ Opinión del juez-LLM (solo comparación, no decide nada)». El resumen por modelo agrega
cuántos «correcta» dio el juez, en cuántos coincidió con el chequeo y sus falsos negativos/positivos
(`ResumenModelo.juez_*`). El log (`a_texto`) dice el árbitro de la corrida y rotula cada veredicto igual.

**Decisiones propias de esta fase:**
- El prompt de diagnóstico **no tiene frases de ejemplo literales** (en la Fase 24 el juez de qwen copió tal cual
  «te quedaste con el conteo absoluto en vez de dividir…» en un caso al que no aplicaba). Es un segundo cambio
  respecto de la Fase 24 en los intentos fallidos: la comparación de abajo mide los dos juntos.
- La cadena **no se corta** cuando un intento acierta: se corren los 5 intentos igual, para que las tablas sean
  comparables con la Fase 24 y para medir si el modelo se mantiene en la respuesta correcta.
- El chequeo evalúa el **resultado de la consulta**, no la redacción: un intento con el SQL correcto y una
  redacción que redondea («10 productos» en vez de 10.11) cuenta como correcto. La validación de cifras del
  pipeline (Fase 6) sigue protegiendo la redacción de cifras inventadas.
- Se corrieron **3 repeticiones** por modelo y árbitro (la del juez r1 es la de la Fase 24), porque a temperatura
  0.5 una sola corrida de 10 × 5 es ruidosa: con un solo par de corridas no se puede separar el efecto del
  cambio del azar.

### 3. Resultados reales: 10 necesidades × 5 intentos, autocorrección

```
python -m src.llm.iteraciones_juez todas --iteraciones 5 --modo autocorreccion --arbitro chequeo --pequeno llama3.1:8b --grande - --timeout 180 --out docs/evidencia/fase25/chequeo-r1/llama3.1-8b
python -m src.llm.iteraciones_juez todas --iteraciones 5 --modo autocorreccion --arbitro chequeo --pequeno qwen2.5-coder:latest --grande - --timeout 180 --out docs/evidencia/fase25/chequeo-r1/qwen2.5-coder-7b
```

Repeticiones con `chequeo-r2/r3` y `--arbitro juez` en `juez-r2/r3`. «juez r1» es la Fase 24. Temperatura 0.5
(juez 0.0), RTX 5060 8 GB, ~5 min por corrida de 50 intentos. Todas las coincidencias se recalcularon a partir de
las filas guardadas, con la regla final de `rf.coincide_con_referencia` (la misma para las 12 corridas). El 14B
no se tocó. Tablas completas: `docs/evidencia/fase25/comparacion.md`.

**Comparación pedida: Fase 24 vs Fase 25, la primera corrida de cada una**

| Modelo | | Necesidades que llegan a coincidir | Aciertan en el intento 1 | Coincidencias /50 | Regresiones ✓→✗ | Estables tras converger |
|---|---|---|---|---|---|---|
| llama3.1:8b | Fase 24 (árbitro juez) | 10/10 | 7/10 | 40/50 | 3 | 8/10 |
| llama3.1:8b | Fase 25 (árbitro chequeo) | 9/10 | 6/10 | 37/50 | 4 | 6/9 |
| qwen2.5-coder 7B | Fase 24 (árbitro juez) | 9/10 | 7/10 | 36/50 | 3 | 6/9 |
| qwen2.5-coder 7B | Fase 25 (árbitro chequeo) | 7/10 | 7/10 | 35/50 | 0 | 7/7 |

Chequeo por intento (✓ = coincide con la referencia):

| Nec. | llama · Fase 24 | llama · Fase 25 | qwen · Fase 24 | qwen · Fase 25 |
|---|---|---|---|---|
| N1 | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✗ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ |
| N2 | 1✗ 2✗ 3✗ 4✗ 5✓ | 1✗ 2✗ 3✓ 4✗ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ |
| N3 | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ |
| N4 | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✗ 5✓ | 1✗ 2✗ 3✓ 4✓ 5✓ | 1✗ 2✗ 3✗ 4✗ 5✗ |
| N5 | 1✗ 2✗ 3✓ 4✓ 5✓ | 1✗ 2✗ 3✗ 4✗ 5✗ | 1✗ 2✗ 3✗ 4✓ 5✓ | 1✗ 2✗ 3✗ 4✗ 5✗ |
| N6 | 1✓ 2✓ 3✓ 4✓ 5✗ | 1✗ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✗ 5✗ | 1✓ 2✓ 3✓ 4✓ 5✓ |
| N7 | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ |
| N8 | 1✗ 2✓ 3✗ 4✓ 5✗ | 1✗ 2✓ 3✗ 4✓ 5✗ | 1✗ 2✗ 3✗ 4✗ 5✗ | 1✗ 2✗ 3✗ 4✗ 5✗ |
| N9 | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ |
| N10 | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ | 1✓ 2✓ 3✗ 4✓ 5✓ | 1✓ 2✓ 3✓ 4✓ 5✓ |

Con este solo par de corridas, la convergencia **baja** (llama 10 → 9, qwen 9 → 7). Lo que sí se ve es el efecto
buscado sobre las regresiones de la Fase 24: qwen N6 (✓✓✓✗✗ → ✓✓✓✓✓) y qwen N1/N10 ya no pierden la respuesta.

**Con 3 repeticiones por modelo y árbitro** (el acierto del intento 1 no depende del árbitro, porque el prompt
del intento 1 es idéntico, así que su variación entre brazos mide el ruido: ±1 necesidad):

| Modelo | Árbitro | Corridas | Necesidades que llegan a coincidir (media) | Aciertan en el 1 | Coincidencias /50 | Regresiones ✓→✗ | Estables tras converger |
|---|---|---|---|---|---|---|---|
| llama3.1:8b | juez | 3 | 9.0 (10, 8, 9) | 6.3 | 35.7 (40, 30, 37) | 4.0 | 59% |
| llama3.1:8b | **chequeo** | 3 | **9.7** (9, 10, 10) | 6.3 | **38.7** (37, 40, 39) | 4.0 | **72%** |
| qwen2.5-coder 7B | juez | 3 | **9.0** (9, 8, 10) | 7.7 | **40.0** (36, 39, 45) | 1.3 | 85% |
| qwen2.5-coder 7B | chequeo | 3 | 8.0 (7, 8, 9) | 6.7 | 36.7 (35, 37, 38) | 1.0 | 88% |

Transiciones entre intentos consecutivos (las 3 corridas juntas):

| Modelo | Árbitro | Intento fallido que ejecutó → ✓ en el siguiente | Error de ejecución → ✓ | Intento ✓ → ✗ en el siguiente |
|---|---|---|---|---|
| llama3.1:8b | juez | 13/27 (48%) | 3/9 | 12/84 (14%) |
| llama3.1:8b | chequeo | **19/29 (66%)** | 0/1 | 12/90 (13%) |
| qwen2.5-coder 7B | juez | 4/23 (17%) | 2/2 | 4/95 (4%) |
| qwen2.5-coder 7B | chequeo | 4/30 (13%) | 1/2 | 3/88 (3%) |

Necesidades que llegan a coincidir, en cuántas de las 3 corridas (solo las que no son 3/3 en todos los casos):

| Nec. | llama · juez | llama · chequeo | qwen · juez | qwen · chequeo |
|---|---|---|---|---|
| N4 | 3/3 | 3/3 | 3/3 | **1/3** |
| N5 | 1/3 | 2/3 | 2/3 | 1/3 |
| N8 | 3/3 | 3/3 | 1/3 | 1/3 |
| N10 | 2/3 | 3/3 | 3/3 | 3/3 |

**Transparencia del juez-LLM (medición aparte, árbitro «chequeo», 3 corridas):**

| Modelo | Juez «correcta» | De acuerdo con el chequeo | Falsos negativos (✓ que el juez no dio por correcto) | Falsos positivos |
|---|---|---|---|---|
| llama3.1:8b | 4/148 | 36/148 (24%) | 112 | 0 |
| qwen2.5-coder 7B | 66/146 | 102/146 (70%) | 44 | 0 |

Igual que en la Fase 24: el juez **nunca** dio por correcto algo que estaba mal (0 falsos positivos en 294
opiniones) y rechazó muchísimas respuestas correctas, sobre todo el de 8B. En los 240 textos de feedback que se
pasaron en las 6 corridas «chequeo»: **0 fugas** y 0 oraciones omitidas por el saneo.

### 4. ¿Mejora la convergencia al quitar al juez como árbitro?

**No de forma clara.** Depende del modelo y la diferencia está dentro del ruido:
- **llama3.1:8b: mejora leve.** Media de 9.7 vs. 9.0 necesidades que convergen, 38.7 vs. 35.7 coincidencias y
  72 % vs. 59 % de necesidades estables tras converger. El cambio más claro es en la corrección desde un intento
  fallido que ejecutó: 66 % vs. 48 %. Con el juez como árbitro, el 8B recibía «incorrecta» sobre respuestas
  correctas (falsos negativos) y diagnósticos inventados; con el chequeo, los diagnósticos solo se piden donde de
  verdad hay un error.
- **qwen2.5-coder 7B: no mejora (8.0 vs. 9.0, 36.7 vs. 40.0).** Su juez ya acertaba el 79 % de las veces como
  árbitro, así que había menos ruido que quitar. La diferencia la hacen N4 y N5, que con el chequeo convergen menos.
  N4 es el caso nuevo más instructivo. En chequeo-r1 los 5 intentos tienen el mismo SQL, que excluye los productos
  con 0 recompras (`HAVING … AND SUM(reordered=1) > 0`), y el diagnóstico de los 5 dice **«Falta el filtro
  'SUM(CASE WHEN op.reordered = 1 …) > 0' en el HAVING»**: el juez invirtió la diferencia y le pidió agregar
  justo el filtro que causaba el error. El modelo quedó anclado. En la Fase 24 ese mismo diagnóstico citaba el
  producto correcto, el saneo lo omitió entero, y con el aviso genérico el modelo se corrigió solo en el intento 3.
- Las **regresiones ✓→✗ no bajan** (llama 13 % vs. 14 %, qwen 3 % vs. 4 %), aunque ahora el intento siguiente
  recibe «CORRECTA… conserva el enfoque» y su propio SQL correcto. Ejemplo: llama N8 chequeo-r1, intento 2 ✓
  (`COUNT(*)`); el 3 recibe «conserva el enfoque» y vuelve a `COUNT(DISTINCT op.product_id)` ✗; el 3 recibe un
  diagnóstico **correcto** («cambiar COUNT(DISTINCT op.product_id) a COUNT(*)») y el 4 vuelve a ✓. **Esto corrige
  una lectura de la Fase 24:** ahí atribuí regresiones como la de llama N8 al diagnóstico falso del juez; con el
  chequeo como árbitro la tasa de regresión es la misma, así que la mayoría viene del muestreo a temperatura 0.5,
  no del juez. (Casos como llama N6 en la Fase 24, que obedeció un diagnóstico falso, sí eran del juez.)

**Conclusión.** Usar el chequeo automático como árbitro es correcto y se deja así: elimina los veredictos falsos
(el juez 8B no dio por correctas 112 de las 116 respuestas correctas que opinó), y los diagnósticos ahora solo se piden donde hay un error real.
Pero no alcanza para mejorar la convergencia de forma consistente, porque ahora los cuellos de botella son otros dos:
1. **La calidad del diagnóstico** cuando el intento sí está mal: de 8B/7B a veces es correcto y efectivo (llama
   N8), a veces es al revés (qwen N4).
2. **La inestabilidad después de acertar**: con temperatura 0.5, ~13 % de los aciertos de llama se pierden en el
   intento siguiente.

**Siguientes pasos sugeridos (no implementados):**
(a) **cortar la cadena** al primer ✓ del chequeo, o reusar ese SQL tal cual en vez de volver a generarlo; esto
eliminaría las regresiones por construcción;
(b) **diagnósticos deterministas** donde se pueda (por ejemplo: «tu HAVING tiene una condición que el resultado
correcto no tiene», «cuentas valores distintos donde la pregunta pide unidades»), comparando el SQL del intento
con el de referencia sin revelar valores;
(c) **pasarle al juez de diagnóstico el SQL de los dos lados ya alineados por cláusula**, para que no invierta la
diferencia.

### 5. Verificación

`python -m pytest -q` (completo):

```
FAILED tests/test_app_instacart.py::test_modelos_cargados_lee_api_ps - Assert...
FAILED tests/test_generar_ficha_dataset.py::test_construir_ficha_coincide_con_dataset_instacart_md
2 failed, 525 passed, 2 xpassed in 138.83s (0:02:18)
```

Son las 2 fallas del entorno que ya estaban antes de la Fase 24 (la librería `ollama` de esta máquina devuelve
`expires_at` como `datetime`; la ficha imprime `data\instacart` con separador de Windows). Lo nuevo en tests:
- `tests/test_fase25_arbitro.py` (17): el chequeo decide aunque el juez diga lo contrario (en los dos sentidos);
  el diagnóstico solo se pide si falló y su prompt no pide veredicto; `--sin-medir-juez`; pipeline fallido sin
  juez; retroalimentación tras un acierto («conserva el enfoque», sin diagnóstico ni detalle del chequeo);
  conteos de falsos negativos/positivos del juez; modo independiente sin cambios; `--arbitro juez` reproduce la
  Fase 24; parseo del diagnóstico; panel con el veredicto del chequeo y la opinión del juez aparte.
- `test_retroalimentacion_no_contiene_valores_de_la_referencia` (Fase 24) ahora corre 3 escenarios × 10
  necesidades contra la BD real: árbitro juez, árbitro chequeo con fallo forzado (el diagnóstico cita la respuesta
  y se sanea) y árbitro chequeo que acierta (sin diagnóstico).
- Los tests de las Fases 11 y 23 que prueban el juez como árbitro pasan `arbitro="juez"` (el comportamiento que
  prueban no cambió). El test del botón del panel de la Fase 11 corre en modo independiente.

## Fase 26 — Corte de la autocorrección en el primer acierto

> Antes de esta fase se ordenó el historial: `ee122e5 fase24` solo tenía las eliminaciones; encima van
> `698c232` (la Fase 24 completa) y `5c6f2f5` (Fase 25), publicados en `origin/IteracionesLap` sin reescribir
> nada. Evidencia de esta fase (ignorada por git): `docs/evidencia/fase26/r{1,2,3}/{llama3.1-8b,qwen2.5-coder-7b}/`
> y `docs/evidencia/fase26/comparacion.md`.

### 1. Por qué

La Fase 25 mostró que las regresiones (un intento correcto seguido de uno incorrecto) **no** las causaba el juez
ni el árbitro, sino el muestreo a temperatura 0.5. Con el chequeo como árbitro, el intento siguiente a un acierto
recibe «CORRECTA… conserva el enfoque» y su propio SQL correcto, y aun así llama perdía el acierto el 13 % de las
veces (12/90) y qwen el 3 % (3/88). Cada intento extra después de acertar solo podía empeorar el estado final.

### 2. El cambio (solo control de flujo)

- `correr_iteraciones`: en modo autocorrección, después de cada intento, si su veredicto es «correcta», se
  registra `corrida.convergencia[modelo] = k` y se sale del bucle de ese modelo. Con el árbitro «chequeo» (el
  default) ese veredicto es el del chequeo automático; con `--arbitro juez`, el «correcta» del juez. Cada modelo
  tiene su propio corte: el grande sigue su cadena aunque el pequeño ya haya convergido.
- Los intentos que faltan quedan **«no ejecutada (ya convergió en el intento k)»**, no como fallidos ni vacíos:
  - `CorridaIteraciones.no_ejecutadas(modelo)` y `total_no_ejecutadas`;
  - el JSON tiene `corta_al_acertar`, `convergencia` y una lista `no_ejecutadas` (modelo, número, etiqueta,
    estado). `iteraciones` solo trae los intentos ejecutados;
  - el log `.txt` y el CLI tienen una línea por intento no ejecutado, con ese texto, y «X de N intentos
    ejecutados, Y no ejecutados porque ya había convergido»;
  - en el panel hay una tarjeta ⏭️ por intento no ejecutado («no falló, no se corrió»), la secuencia
    `1❌ → 2✅ → 3⏭️ → 4⏭️ → 5⏭️` y, como métrica principal del modelo, «✅ Convergió en el intento k» o
    «❌ No convergió (5 de 5 intentos)» en vez del % de correctas, que con corte deja de tener sentido (hay a lo
    sumo un «correcta» por modelo).
- `completa` cuenta lo no ejecutado: una cadena cortada **no** se muestra como «corrida interrumpida» (aviso de la
  Fase 23). Una interrupción antes de converger sigue quedando «en curso».
- `ResumenModelo` agrega `no_ejecutadas` y `estado_final` (el veredicto del último intento ejecutado).
- `--sin-corte` (CLI) o `corta_al_acertar=False` reproducen la Fase 25.

**Modo independiente: el corte no aplica** (lancé `ValueError` si se pide). Ese modo existe para medir la
variabilidad: N muestras desde cero de la misma pregunta. Cortar en el primer acierto eliminaría justo esas
muestras (si acierta en el intento 1 no queda ninguna más) y sesgaría el % de correctas. Además ahí no hay nada
que proteger: los intentos no se influyen, y uno extra no puede «perder» un acierto anterior.

### 3. Resultados reales (10 necesidades × hasta 5 intentos, 3 repeticiones por modelo)

```
python -m src.llm.iteraciones_juez todas --iteraciones 5 --modo autocorreccion --pequeno llama3.1:8b --grande - --timeout 180 --out docs/evidencia/fase26/r1/llama3.1-8b
python -m src.llm.iteraciones_juez todas --iteraciones 5 --modo autocorreccion --pequeno qwen2.5-coder:latest --grande - --timeout 180 --out docs/evidencia/fase26/r1/qwen2.5-coder-7b
```

Igual en r2 y r3. Árbitro chequeo (default), medición del juez activa, temperatura 0.5. El 14B no se tocó. «Termina
en correcto» = el último intento ejecutado coincide con la referencia (chequeo automático, misma regla para todas
las corridas, recalculado desde las filas guardadas). Los brazos sin corte son las corridas de la Fase 25 y las
repeticiones del brazo juez (la «juez r1» es la Fase 24).

**¿Cuántas necesidades terminan en estado correcto?** (media de 3 repeticiones; entre paréntesis, cada una)

| Modelo | Brazo | Terminan en correcto | Acertaron alguna vez | Intentos ejecutados | Tiempo por corrida |
|---|---|---|---|---|---|
| llama3.1:8b | juez, sin corte (Fases 24/25) | 7.7 (8, 7, 8) | 9.0 (10, 8, 9) | 50 | 5.0 min |
| llama3.1:8b | chequeo, sin corte (Fase 25) | 8.7 (8, 9, 9) | 9.7 (9, 10, 10) | 50 | 4.9 min |
| llama3.1:8b | **chequeo + corte (Fase 26)** | **9.7** (10, 10, 9) | 9.7 (10, 10, 9) | **16.0** | **1.6 min** |
| qwen2.5-coder 7B | juez, sin corte (Fases 24/25) | 8.3 (8, 8, 9) | 9.0 (9, 8, 10) | 50 | 5.0 min |
| qwen2.5-coder 7B | chequeo, sin corte (Fase 25) | 7.3 (7, 7, 8) | 8.0 (7, 8, 9) | 50 | 5.2 min |
| qwen2.5-coder 7B | **chequeo + corte (Fase 26)** | **8.7** (9, 8, 9) | 8.7 (9, 8, 9) | **18.3** | **2.2 min** |

Por necesidad, en cuántas de las 3 repeticiones termina en correcto:

| Nec. | llama · juez | llama · chequeo | llama · corte | qwen · juez | qwen · chequeo | qwen · corte |
|---|---|---|---|---|---|---|
| N1 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| N2 | 3/3 | 2/3 | 3/3 | 3/3 | 2/3 | 3/3 |
| N3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| N4 | 3/3 | 3/3 | 3/3 | 3/3 | 1/3 | **0/3** |
| N5 | 1/3 | 2/3 | 2/3 | 2/3 | 1/3 | 2/3 |
| N6 | 0/3 | 3/3 | 3/3 | 2/3 | 3/3 | 3/3 |
| N7 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| N8 | 2/3 | 1/3 | 3/3 | 0/3 | 0/3 | 3/3 |
| N9 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| N10 | 2/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |

Secuencias de la repetición 1 (⏭ = no ejecutado porque ya convergió):

| Nec. | llama3.1:8b | qwen2.5-coder 7B |
|---|---|---|
| N1 | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ |
| N2 | 1✗ 2✗ 3✓ 4⏭ 5⏭ | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ |
| N3 | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ |
| N4 | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ | 1✗ 2✗ 3✗ 4✗ 5✗ |
| N5 | 1✗ 2✓ 3⏭ 4⏭ 5⏭ | 1✗ 2✗ 3✓ 4⏭ 5⏭ |
| N6 | 1✗ 2✓ 3⏭ 4⏭ 5⏭ | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ |
| N7 | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ |
| N8 | 1✗ 2✓ 3⏭ 4⏭ 5⏭ | 1✗ 2✗ 3✓ 4⏭ 5⏭ |
| N9 | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ |
| N10 | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ | 1✓ 2⏭ 3⏭ 4⏭ 5⏭ |

### 4. Lectura

- **El corte resuelve lo que debía resolver.** Las necesidades que terminan en correcto suben de 8.7 a **9.7**
  (llama) y de 7.3 a **8.7** (qwen) frente a la Fase 25, y de 7.7/8.3 frente al brazo juez. Con corte, «termina
  en correcto» = «acertó alguna vez» por construcción: ya no se pierde ningún acierto. Es la mejora más grande de
  las Fases 24-26 y con el costo más bajo: un tercio de los intentos (16-18 de 50) y de 2 a 3 veces menos tiempo.
- **Lo que el corte NO cambia.** Hasta el primer acierto, el proceso es idéntico al de la Fase 25 (mismos prompts,
  misma retroalimentación), así que el corte no hace que el modelo acierte **más**, solo que no pierda lo que
  acertó. La cifra esperada era la columna «acertaron alguna vez» de la Fase 25 (9.7 y 8.0), y salió 9.7 y 8.7.
  La diferencia de qwen está dentro del ruido: qwen N8 convergió en 3/3 ahora y en 1/3 en la Fase 25, con el mismo
  proceso.
- **Lo que queda sin resolver es determinista, no ruido.** qwen N4: 0/3. En las 3 repeticiones, los 15 intentos
  llevan el mismo error (`HAVING … AND SUM(reordered = 1) > 0`, que excluye los productos con 0 recompras), y el
  diagnóstico del juez sigue invertido: «Falta el filtro 'SUM(…) > 0' en el HAVING», el mismo de la Fase 25. N5
  (tamaño del carrito) es el otro punto débil de los dos modelos (2/3). Esto es lo que atacarían los diagnósticos
  deterministas (comparar el SQL del intento con el de referencia, cláusula por cláusula), que quedaron para
  después.

### 5. Verificación

`python -m pytest -q` (completo):

```
FAILED tests/test_app_instacart.py::test_modelos_cargados_lee_api_ps - Assert...
FAILED tests/test_generar_ficha_dataset.py::test_construir_ficha_coincide_con_dataset_instacart_md
2 failed, 535 passed, 2 xpassed in 141.27s (0:02:21)
```

Son las mismas 2 fallas del entorno de las Fases 24-25 (la versión de la librería `ollama` y el separador de rutas de Windows).
`tests/test_fase26_corte.py` (10 tests):
- corta en el primer acierto, sin volver a llamar al modelo, y marca el resto como no ejecutado;
- JSON, log y CLI con «no ejecutada (ya convergió en el intento k)»;
- sin acierto corre los N intentos;
- cada modelo tiene su propio corte;
- `--sin-corte` reproduce la Fase 25;
- el modo independiente nunca corta y rechaza `corta_al_acertar=True`;
- con árbitro juez corta en el «correcta» del juez;
- una interrupción antes de converger sigue «en curso»;
- el panel muestra «Convergió en el intento k», las tarjetas ⏭️ y la secuencia.

Los tests de fases anteriores que observan lo que pasa **después** de un acierto pasan `corta_al_acertar=False`
(el comportamiento que prueban no cambió).

## Fase 27 — Bitácora de aprendizaje del chat libre y revisión de UX

> Evidencia (ignorada por git): `docs/evidencia/fase27/`:
> - `antes/` y `despues/`: capturas del chat y del dashboard;
> - `experimento_bitacora.jsonl`: el experimento con y sin la lección;
> - `revisor_real.txt`: el revisor de SQL con 8B/7B sobre la BD real.
>
> La bitácora real vive en `data/cache/bitacora_chat.json`.

### Parte 1 — Bitácora de aprendizaje del chat libre

**El problema (reproducido en vivo, antes del cambio, con qwen2.5-coder:1.5b):** dos preguntas distintas en
la misma sesión repetían la misma confusión de esquema.

- «¿Cuántos pasillos tiene cada departamento?» → `JOIN aisles a ON a.department_id = d.department_id` →
  `Binder Error: Table "a" does not have a column named "department_id"`. El reintento repitió el error.
- «¿Qué departamento tiene más pasillos distintos?» → otra variante del mismo malentendido
  (`Referenced table "d" not found`). El chat no había aprendido nada de la primera.

**Diferencia con el panel de necesidades.** En el chat libre no hay respuesta de referencia, así que nadie puede
decir si la respuesta final es «correcta». Lo que sí se puede aprender entre preguntas son los errores del SQL
contra el esquema real. Por eso el juez del chat solo revisa la **sentencia**, nunca la respuesta.

**Qué se implementó:**

1. **`src/llm/bitacora_chat.py`**: bitácora en disco, `data/cache/bitacora_chat.json` (ignorado por git), que
   sobrevive entre sesiones de Streamlit. Se lee en cada pregunta (es chica) y se escribe de forma atómica.
   - Cada entrada (`Leccion`) guarda: `pregunta`, `sql`, `detalle` (el error real o el motivo del revisor,
     recortado a 400 caracteres), `leccion` (una frase), `origen`, `firma`, `veces`, `primera_vez` y `ultima_vez`.
   - Se deduplica por `firma`: el mismo error de esquema suma `veces` en vez de repetirse.
2. **Lecciones deterministas a partir del error real de DuckDB** (`leccion_de_error`), sin modelo, con el esquema
   real (`DESCRIBE`) y los alias del SQL:
   - columna en la tabla equivocada → «La tabla aisles NO tiene la columna department_id (sus columnas:
     aisle_id, aisle); department_id está en: departments, products.»;
   - columna inexistente → dónde sí está;
   - alias sin declarar;
   - tabla inexistente → la lista de tablas reales;
   - agregación anidada → la forma con subconsulta;
   - GROUP BY incompleto;
   - timeout → la pista de la Fase 24;
   - cualquier otro error → «Un SQL anterior falló con: <primera línea>».

   Solo se aprende de errores de **ejecución** (DuckDB o el timeout de la guarda). El modelo caído y la guarda que
   rechaza una escritura no enseñan nada del esquema.
3. **`src/llm/revision_sql.py`, revisor de la sentencia**: si el SQL ejecutó, el mismo modelo del chat (temperatura
   0, JSON) revisa solo la sentencia, frente al esquema real (`DESCRIBE`), la pregunta y las primeras 5 filas:
   - ¿tiene sentido para la pregunta?;
   - ¿cada columna está en su tabla y cada JOIN une por columnas que existen en ambas?;
   - ¿hay señales raras? (promediar un id, agregar la columna equivocada, un filtro o un LIMIT que la pregunta no
     pide).

   Devuelve `{"sospechoso", "motivo", "leccion"}`. Se le pide prudencia («ante la duda, false»), y un
   «sospechoso» sin motivo, o una respuesta ilegible, cuentan como no sospechoso.
4. **Integración** (`responder_pregunta_instacart(..., bitacora=, revisar_sql=)`; solo el chat los pasa, las
   necesidades y la entrega no cambian):
   - las lecciones van al **final del prompt de sistema** de SQL (y de su reintento), después de
     `REGLAS_DE_NEGOCIO`, bajo «LECCIONES APRENDIDAS de errores reales en preguntas anteriores de este chat…»;
     sin lecciones, el prompt es idéntico al de antes;
   - el reintento sigue siendo **uno solo**. Si el primer SQL falló, se usa como antes. Si el SQL ejecutó y el
     revisor lo marca sospechoso, el único reintento se usa con su motivo («Un revisor marcó tu SQL como
     sospechoso: …»). Si ese reintento no ejecuta, **se conserva el resultado original**;
   - al terminar la fase SQL (bien o mal) se agregan las lecciones de la pregunta: del error real del primer
     intento, del error final, de la revisión sospechosa y del reintento fallido. Se deduplican por firma dentro
     de la misma pregunta, así que el mismo error en los dos intentos cuenta una vez. La bitácora nunca tumba una
     respuesta: si algo falla al aprender, no se aprende;
   - la app pasa `Bitacora()` (lee el disco en cada pregunta, así que lo guardado se usa desde la primera pregunta
     de una sesión nueva) y `revisar_sql` según ⚙️.
5. **UI**:
   - en «🔎 Detalles técnicos» de cada respuesta se ven: la revisión de la sentencia (sin problemas / sospechosa y
     qué pasó con el reintento), cuántas lecciones llevaba el prompt y la lección agregada;
   - en ⚙️ «Aprendizaje del chat» están: el interruptor del revisor (activo por defecto), la bitácora con sus
     lecciones (📌 = va en el prompt; ×N = veces) y el botón «🗑️ Borrar la bitácora».

**Límites de tamaño (criterio).** El prompt de sistema de SQL ya mide ~8.4k caracteres (~2.5k tokens) y los
modelos corren con contexto de 4,096 tokens. La bitácora no puede competir con el esquema ni con la pregunta:
- **en el prompt**: como máximo **8 lecciones** y **1,900 caracteres** para todo el bloque (~500 tokens, ~12 % del
  contexto). Cada lección se recorta a **220 caracteres** (una frase). Entran las de más `veces` y, a igualdad,
  las más recientes: un error que se repite es el que más vale recordar;
- **en disco**: como máximo **20**. Al pasarse, sale la de menos `veces` y, a igualdad, la más antigua. Así queda
  margen para que una lección nueva compita con las del prompt sin que el archivo crezca sin límite;
- el empate de `ultima_vez` (resolución de segundos) se rompe por orden en el archivo. El test del límite encontró
  que sin eso se descartaba la recién agregada en vez de la más vieja.

**Caso real en el chat (con la app, después del cambio, qwen2.5-coder:1.5b, bitácora vacía al empezar):**
1. «¿Cuántos pasillos tiene cada departamento?» → el mismo `Binder Error` de `a.department_id`. En disco queda:
   ```
   "firma": "columna_en_tabla:aisles.department_id",
   "leccion": "La tabla aisles NO tiene la columna department_id (sus columnas: aisle_id, aisle); department_id está en: departments, products.",
   "pregunta": "¿Cuántos pasillos tiene cada departamento?", "veces": 1
   ```
   En los detalles se lee: «📓 Lección agregada a la bitácora: La tabla aisles NO tiene la columna department_id…».
2. «¿Qué departamento tiene más pasillos distintos?» (pregunta distinta) → en los detalles: «📓 El prompt de
   esta pregunta llevaba 1 lección(es) de la bitácora». **El 1.5b repitió el error de todos modos**
   (`JOIN aisles a ON d.department_id = a.department_id`). La bitácora lo registró como `veces: 2`, sin duplicar.

**¿La lección cambia el SQL? Experimento controlado** (BD real, temperatura 0, revisor apagado para aislar la
lección, bitácoras temporales). Las 4 preguntas son: pasillos por departamento, departamento con más pasillos,
pasillos de produce y pasillos de dairy eggs.

| Modelo | Sin lección: OK / cometen el error `aisles.department_id` | Con la lección: OK / cometen el error |
|---|---|---|
| qwen2.5-coder:1.5b | 2/4 · 1/4 | 2/4 · 1/4 |
| llama3.1:8b | 4/4 · 0/4 | 4/4 · 0/4 |
| qwen2.5-coder 7B | 3/4 (una tras reintento) · **2/4** | **4/4, todas al primer intento · 0/4** |

- **7B: la lección elimina el error.** Pasillos por departamento falla sin ella y sale bien con ella (personal
  care 17, pantry 12…). En «dairy eggs», sin ella el primer intento comete el error y el reintento lo arregla; con
  ella sale al primer intento.
- **8B**: no comete ese error; la lección no cambia nada.
- **1.5b**: no la aprovecha. Con la lección cambia de error (alias `d` sin declarar) o lo repite en otra pregunta.
  Es coherente con lo visto en la Fase 23: el 1.5b casi no usa el contexto que se le da.

Advertencia que aparece en la tabla: «OK» quiere decir que ejecutó, no que sea correcto. Por ejemplo, llama
resuelve «dairy eggs» con `department_id = 6` fijo (el real es 16) y devuelve los pasillos de otro departamento.

**El revisor con modelos reales** (`revisor_real.txt`, llama3.1:8b y qwen2.5-coder 7B, 2 preguntas cada uno):
- **Sin falsos positivos** en los 3 SQL correctos.
- **llama, «dairy eggs»**: marcó como sospechoso el SQL con `department_id = 6`, que efectivamente está mal, pero
  con un motivo impreciso: «el filtro en el JOIN no está bien escrito», no «el id está inventado». El reintento
  falló con el `Binder Error` de `aisles.department_id` y se conservó el resultado original, que sigue siendo
  incorrecto. Quedaron 2 lecciones:
  - la determinista del error del reintento (precisa);
  - la del revisor, genérica y algo engañosa: «Los filtros en los JOIN deben estar escritos como condiciones de
    igualdad entre las columnas de ambas tablas.».

  **Riesgo:** las lecciones del revisor son de peor calidad que las deterministas y también entran al prompt.
  Pendiente sugerido: darles menos prioridad (o un tope propio), o detectar de forma determinista los ids fijos
  (`department_id = <número>`) contra el nombre que da la pregunta.

**Tests** (`tests/test_fase27_bitacora.py`, 28):
- lección determinista del error real de la revisión (`aisles.department_id`, con DuckDB de verdad) y de los
  otros 5 errores de esquema;
- persistencia entre instancias, dedup y archivo corrupto;
- límites (20 en disco, 8 y 1,900 caracteres en el prompt, 220 por lección; descarte de la menos repetida y más
  vieja);
- prompt idéntico sin lecciones;
- **una pregunta falla por el error de esquema conocido → se guarda la lección → una pregunta DISTINTA posterior
  la recibe en su prompt de sistema**, tanto en el responder como en la app, en la misma sesión;
- la bitácora se carga del disco al iniciar la app;
- revisor sospechoso → reintento y lección; sin problemas → nada; reintento fallido → se conserva el original;
- no se aprende de la guarda ni del modelo caído;
- sin bitácora ni revisor, nada cambia (necesidades y entrega);
- el botón para borrar y el interruptor del revisor.

`tests/conftest.py` aísla la bitácora en un archivo temporal para toda la suite: ningún test toca la real. En el
doble del modelo de los tests de la app, las llamadas del revisor se reconocen por su prompt y se registran
aparte (`revisiones_hechas`), así que los tests que comparan la lista exacta de llamadas no cambiaron.

### Parte 2 — Rediseño visual (sin tocar la lógica)

Capturas en `docs/evidencia/fase27/antes/` (01-08) y `despues/` (01-09), en el mismo panel (622 px de ancho) y
con las mismas preguntas.

| Problema (antes) | Cambio (después) |
|---|---|
| En el chat, preguntas y respuestas seguidas sin límite claro entre un turno y otro | Cada pregunta con su respuesta en una **tarjeta con borde** (`st.container(border=True)`) |
| El error de DuckDB partido en párrafos sueltos («Candidate bindings», «LINE 3…») | Primera línea como texto y el resto en un **bloque de código** dentro de la misma alerta |
| «Detalles» con captions seguidas, que se leían como un solo párrafo | «🔎 Detalles técnicos» con secciones rotuladas: **SQL ejecutado · Primer intento rechazado** (con el error en bloque de código) **· Revisión y bitácora · Datos** |
| Los colapsables se veían como un párrafo más | **Todos los expanders** con borde **punteado**, fondo tenue y título más chico (`src/ui/estilos.py`), distintos de las tarjetas de borde sólido |
| Jerarquía plana: la necesidad («N1 — …») en `st.title`, del mismo tamaño que el título de la página | `st.title` = página (💬 chat / 📊 dashboard) · `st.header` = necesidad · `st.subheader` = sección de tarjeta · negrita = rótulo · `caption` = metadatos |
| Dashboard: el título, el selector, «Actualizar datos» y el PDF en un mismo bloque; luego la Entrega y las Iteraciones **antes** que la necesidad | Título y selector arriba; herramientas en un colapsable «🛠️ Datos y reporte PDF»; divisor; **la necesidad**; divisor; «🧰 Evaluación de la IA» (Iteraciones y Entrega) al final |
| Panel con subtítulos y texto corrido | Tarjetas en el mismo orden en las 10 necesidades: «❓ Pregunta / 🎯 Decisión» · «✅ Respuesta» (valores clave + redacción) · «📈 Gráfica» · «⚠️ Limitaciones» (siempre visibles) · «🔎 Detalle técnico» (SQL, resultado crudo y parámetros, colapsados) |
| Espaciado variable | `gap` uniforme entre bloques (0.9rem); las tarjetas usan el padding propio de Streamlit |

**Decisiones:**
- Dentro del colapsable de herramientas no puede haber otro expander ni `st.status` (Streamlit no los anida). El
  detalle del último refresco pasó a captions y el progreso del PDF a `st.spinner`.
- Los tests de UI que buscaban el título de la necesidad en `at.title` buscan ahora `at.header`, y el del chat
  pasó de `at.subheader` a `at.title`. Los de la etiqueta «Detalles» buscan «🔎 Detalles técnicos».
- **No se tocó:** la barra de navegación (💬/📊/⚙️). En un panel angosto (<640 px) Streamlit apila las columnas y
  los tres botones quedan a todo el ancho. Arreglarlo exige cambiar los controles y sus tests; queda como
  pendiente de UX.

### Verificación

`python -m pytest -q` (completo):

```
FAILED tests/test_app_instacart.py::test_modelos_cargados_lee_api_ps - Assert...
FAILED tests/test_generar_ficha_dataset.py::test_construir_ficha_coincide_con_dataset_instacart_md
2 failed, 563 passed, 2 xpassed in 155.79s (0:02:35)
```

Son las mismas 2 fallas del entorno de las fases anteriores. Después de la suite, la bitácora real seguía
intacta (la lección `×2` de la prueba en vivo).

## Fase 28 — Cinco necesidades más analíticas (N1, N2, N8, N9, N10), una consulta cada una

> Mismo patrón que la Fase 24: una sola consulta SQL por necesidad, JOINs según `REGLAS_DE_NEGOCIO`, redacción
> generada por código, forma de respuesta declarada y caché `data/referencia/Nx.json` invalidada por huella de la
> BD + pregunta + SQL. Siguen fuera de alcance las preguntas compuestas y las comparaciones entre dos resultados.
>
> Evidencia (ignorada por git): `docs/evidencia/fase28/` (corrida real con llama3.1:8b).

### 1. Qué cambió

| | Antes (Fase 24) | Ahora (Fase 28) |
|---|---|---|
| N1 | 10 productos más vendidos por unidades | Clientes con la frecuencia de compra más sostenida |
| N2 | Departamento con más pedidos | Departamento con la tasa de recompra promedio más alta |
| N8 | Pasillo con más unidades vendidas | Franja horaria con más pedidos |
| N9 | Producto que más veces va primero al carrito (siempre «Banana», sin valor de negocio) | Los dos productos que más se compran juntos (combo/oferta) |
| N10 | % de líneas recompradas | % de pedidos 100 % recomprados vs. 100 % nuevos |

**N3, N4, N5, N6 y N7 no cambiaron.** Se verificó campo por campo (título, pregunta, decisión, universo,
filtros, limitaciones, criterio de corte, forma, columnas, parámetros y SQL) contra la versión del commit
anterior: idénticas.

**Columnas verificadas antes de escribir el SQL** (`DESCRIBE` de la BD real):
- `orders`: `order_id`, `user_id`, `eval_set`, `order_number`, `order_dow`, `order_hour_of_day` (UTINYINT, 0-23),
  `days_since_prior_order`;
- `order_products`: `order_id`, `product_id`, `add_to_cart_order`, `reordered`;
- `products`: `product_id`, `product_name`, `aisle_id`, `department_id`;
- `departments`: `department_id`, `department`.

Los nombres que pedía el enunciado (`user_id`, `days_since_prior_order`, `order_hour_of_day`) son los reales.

### 2. Las 5 nuevas: SQL, resultado real y decisiones

Todos los resultados salen de ejecutar el SQL contra `data/instacart.duckdb`
(`python -m src.analytics --todas`, tras borrar los `Nx.json` anteriores).

**N1 — Clientes de compra más sostenida** (lista; `user_id` es un identificador numérico).
```sql
SELECT user_id, COUNT(order_id) AS total_pedidos, AVG(days_since_prior_order) AS promedio_dias_entre_pedidos
FROM orders GROUP BY user_id HAVING COUNT(order_id) >= 10
ORDER BY total_pedidos DESC, promedio_dias_entre_pedidos ASC, user_id LIMIT 10
```
- Resultado: cliente 46408 (100 pedidos, 0.23 días), 134572 (0.29), 112044 (0.42), 54485, 100787, 100982,
  191755, 1024, 91263, 172806 (0.71).
- Mínimo de pedidos = 10, la mediana de pedidos por cliente (medida: 110,728 de 206,209 clientes tienen ≥10).
- **Hallazgo:** el dataset guarda como máximo 100 pedidos por cliente y 1,374 clientes llegan a ese tope. El top
  10 está empatado en 100 pedidos y el orden lo decide el promedio de días; la redacción lo dice. Un promedio de
  0.23 días significa varios pedidos al día: la limitación advierte que puede ser una cuenta de negocio más que
  un cliente fiel.
- La pregunta no fija cuántos: `filas_esperadas = None` y 10 por convención, igual que N3/N4.

**N2 — Departamento con la tasa de recompra promedio más alta** (único).
```sql
SELECT d.department, COUNT(*) AS veces_comprado,
       SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) AS recompras,
       SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS tasa_recompra
FROM order_products op JOIN products p ON op.product_id = p.product_id
JOIN departments d ON p.department_id = d.department_id
GROUP BY d.department ORDER BY tasa_recompra DESC, d.department LIMIT 10
```
- Resultado: dairy eggs, 67.0 % (3,773,723 de 5,631,067 líneas); luego beverages 65.4 % y produce 65.1 %.
- Decisión: «tasa promedio» = proporción de líneas recompradas del departamento (el promedio de `reordered`),
  no el promedio de las tasas de sus productos. Es la misma definición de tasa que N3/N4.

**N8 — Franja horaria con más pedidos** (único; la hora es un identificador numérico).
```sql
SELECT order_hour_of_day, COUNT(*) AS pedidos FROM orders
GROUP BY order_hour_of_day ORDER BY pedidos DESC, order_hour_of_day
```
- Resultado: la hora 10, con 288,418 pedidos (8.4 % de 3,421,083); luego las horas 11 (284,728) y 15 (283,639).
- La referencia guarda las 24 horas, no un top 10: así la gráfica muestra el día completo.

**N9 — Los dos productos que más se compran juntos** (único; el identificador es el par de nombres).
```sql
SELECT pa.product_name AS producto_a, pb.product_name AS producto_b, pares.pedidos_juntos
FROM (
    SELECT a.product_id AS id_a, b.product_id AS id_b, COUNT(*) AS pedidos_juntos
    FROM order_products a
    JOIN order_products b ON a.order_id = b.order_id AND a.product_id < b.product_id
    WHERE a.product_id IN (SELECT product_id FROM order_products GROUP BY product_id HAVING COUNT(*) >= 10000)
      AND b.product_id IN (SELECT product_id FROM order_products GROUP BY product_id HAVING COUNT(*) >= 10000)
    GROUP BY a.product_id, b.product_id
) pares
JOIN products pa ON pares.id_a = pa.product_id JOIN products pb ON pares.id_b = pb.product_id
ORDER BY pares.pedidos_juntos DESC, producto_a, producto_b LIMIT 10
```
- Resultado: Bag of Organic Bananas + Organic Hass Avocado, 64,761 pedidos juntos; luego Bag of Organic Bananas
  + Organic Strawberries (64,702) y Organic Strawberries + Banana (58,330).
- **Lo que se midió antes de decidir el SQL:**
  - el self-join directo tal como lo describe el enunciado se queda **sin memoria**: `Out of Memory Error` a los
    79 s con el límite de 3.7 GiB del proyecto;
  - agrupar por `product_id` y traer los nombres después, considerando solo los productos con al menos 10,000
    pedidos, tarda 3.7 s;
  - **la poda es exacta, no una aproximación**: un par con K co-ocurrencias exige K pedidos de cada producto, y el
    par número 10 tiene 42,283 ≥ 10,000, así que ningún par descartado podía entrar al top. Con 5,000 el top sale
    idéntico. La redacción de la referencia afirma la exactitud solo si se cumple esa condición, y hay un test de
    integración que la verifica sobre la BD real.
- **Sin `WITH`:** la guarda del chat (`validar_sql`) exige que la sentencia empiece con `SELECT`. Con un CTE ni la
  referencia ni el SQL de un modelo pasarían por el panel de iteraciones. Con subconsultas en el `FROM` sigue
  siendo una sola sentencia; tarda **6.9 s** con las guardas del chat, cerca del límite de 10 s de la guarda
  (declarado en las limitaciones).
- Es co-ocurrencia por **volumen**, no afinidad (lift): los productos más populares salen juntos por serlo. Está en
  las limitaciones, junto con que no hay precios para saber si conviene como combo.

**N10 — Pedidos 100 % recomprados vs. 100 % nuevos** (escalar con dos cifras).
```sql
SELECT SUM(CASE WHEN recompradas = lineas THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS pct_pedidos_todo_recomprado,
       SUM(CASE WHEN recompradas = 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS pct_pedidos_todo_nuevo,
       SUM(CASE WHEN recompradas = lineas THEN 1 ELSE 0 END) AS pedidos_todo_recomprado,
       SUM(CASE WHEN recompradas = 0 THEN 1 ELSE 0 END) AS pedidos_todo_nuevo, COUNT(*) AS pedidos
FROM (SELECT order_id, COUNT(*) AS lineas, SUM(CASE WHEN reordered = 1 THEN 1 ELSE 0 END) AS recompradas
      FROM order_products GROUP BY order_id) t
```
- Resultado: 21.49 % de los pedidos son 100 % recomprados (719,207) y 11.87 % son 100 % nuevos (397,115), de
  3,346,083 pedidos con productos; el 66.64 % es mixto.
- «Frente a» se responde con las dos cifras en una sola fila, no con dos consultas.
- Limitación: el primer pedido de cada cliente es 100 % nuevo por definición.

### 3. Cambios de infraestructura (reutilizando la de la Fase 24, sin duplicar)

- **`DefNecesidad`** suma tres campos opcionales, con defaults que dejan a N3-N7 igual:
  - `nombre_es_texto` (N1, N8: el identificador es un número);
  - `columnas_nombre_extra` (N9: el par);
  - `columnas_valor_extra` (N10: la segunda cifra).

  `columnas_ordenadas` y la gráfica genérica los usan: el par se grafica como «A + B» en una columna `par`.
- **Chequeo automático** (`retroalimentacion.coincide_con_referencia`), generalizado:
  - los identificadores se comparan como texto normalizado o como entero;
  - en «único», cada identificador de la primera fila se consume antes de buscar la cifra (así la hora 10 no se
    toma por una cifra) y el par se encuentra en cualquier orden;
  - en «escalar», todas las cifras de respuesta tienen que estar.

  `chequeo_forma` no pide «columna de texto» cuando el identificador es numérico, y su mensaje para N10 dice
  «una sola fila (con sus cifras)».
- **Caché:** sin cambios. Los `Nx.json` viejos de N1, N2, N8, N9 y N10 se borraron y se regeneraron; además, la
  invalidación por pregunta + SQL los habría descartado igual. Ojo: un cambio solo en el texto de las
  limitaciones **no** invalida la caché (no está en la firma). Por eso `N9.json` se regeneró a mano después de
  ajustar una limitación.
- **Bug encontrado y corregido:** `pdf_vigente()` comparaba solo la huella de la BD de cada necesidad, que no
  cambia cuando cambia una pregunta. El dashboard habría dicho «✅ El PDF ya está actualizado» con un PDF de las
  preguntas viejas (verificado: el PDF de la Fase 27 se reportaba vigente). El metadato del PDF guarda ahora
  también la `huella_contenido` de cada necesidad, y un metadato anterior sin ese campo se toma como
  desactualizado. Test: `test_pdf_deja_de_estar_vigente_si_cambia_el_contenido_de_una_necesidad`. El PDF real
  se regeneró (444 KB) y está vigente.
- Documentación: `docs/NECESIDADES.md` (secciones, formas, tabla de parámetros con `min_pedidos` = 10 y
  `min_pedidos_producto` = 10,000; el PDF no reporta discrepancias) y `docs/ENTREGA_NECESIDADES.md`.

### 4. Pruebas que fallaron por el cambio de preguntas, y cómo se resolvieron

Primera corrida completa sin tocar ningún test: **33 failed, 532 passed** (de las 33, 2 son las del entorno de
siempre).

| Tests | Por qué fallaron | Qué se hizo |
|---|---|---|
| `test_analytics_necesidades` (12) | Oráculos y valores reales de las 5 preguntas viejas | Oráculos pandas nuevos para N1, N2, N8, N9, N10. Valores reales nuevos. Un test de que cada SQL es una sola sentencia `SELECT` que pasa la guarda. Otro de que el umbral de N9 no cambia el top real |
| `test_fase25_arbitro` (8), `test_fase26_corte` (5) | Usaban «el SQL de N1 acierta en la fixture»; la N1 nueva pide clientes con ≥10 pedidos y la fixture no tiene ninguno | Pasan a usar N5 (lo que prueban, el árbitro y el corte, no depende de la necesidad) |
| `test_fase24_retroalimentacion` (2) | Chequeo de forma y coincidencia con columnas de la N2 vieja y con el «top 10 fijo» de la N1 vieja | Actualizados, más un test nuevo para el `user_id`, la hora, el par y las dos cifras |
| `test_pdf_generador` (1), `test_dashboard_servicio` (1), `test_fase10_entrega` (1), `test_app_instacart` (1) | Palabra clave de la limitación de N2, texto de la pregunta N1, documento de la entrega, panel N9 sin filas en la fixture | Palabras clave nuevas (N1, N2, N5, N8, N9, N10), pregunta tomada de la definición, documento actualizado, N8 en lugar de N9 |
| `test_analytics_honestidad` (1) | Ya ninguna limitación aclaraba la falta de precios (era la de N1/N8 viejas) | N9 la declara: combo u oferta sin precios ni márgenes |

**El test de no-fuga de la Fase 24 contra la BD real (3 escenarios × 10 necesidades) pasó desde la primera
corrida con las 5 nuevas.**

### 5. Corrida real con llama3.1:8b (autocorrección, 3 intentos, árbitro chequeo, corte)

| Nec. | Resultado | Qué hizo el modelo |
|---|---|---|
| N1 | ✗ ✗ ✗ | Intentó un `JOIN LATERAL` con agregados; el primero ejecutó pero no devolvió filas, y los dos siguientes fallaron con un `Binder Error` (GROUP BY, columna ambigua) |
| N2 | ✗ ✗ ✗ | Agrupó por `department_id` sin nombre; en el intento 3 dividió la tasa dos veces (`AVG(...) / COUNT(*)`) |
| N8 | ✓ en el intento 1; 2 y 3 «no ejecutados (ya convergió)» | `GROUP BY order_hour_of_day ORDER BY COUNT(*) DESC LIMIT 1` → la hora 10 (el chequeo la reconoce como identificador numérico) |
| N9 | ✗ ✗ ✗ por **timeout** | El self-join sin poda supera los 10 s de la guarda en los 3 intentos (22-33 s) |
| N10 | ✗ ✗ ✗ | Dos `WHERE` con agregados (`Binder Error`) y en el intento 3 calculó el % de **líneas** recompradas (la N10 vieja) |

Las 5 nuevas son bastante más difíciles para un 8B que las de la Fase 24 (donde convergía en 10/10). Sobre todo
N9: con la guarda de 10 s, el enfoque obvio no puede ejecutar. Se resolvió en la Fase 29 (más abajo).

### 6. Verificación

`python -m pytest -q` (completo, después de los ajustes):

```
FAILED tests/test_app_instacart.py::test_modelos_cargados_lee_api_ps - Assert...
FAILED tests/test_generar_ficha_dataset.py::test_construir_ficha_coincide_con_dataset_instacart_md
2 failed, 579 passed, 2 xpassed in 156.27s (0:02:36)
```

Son las 2 fallas del entorno de siempre (la librería `ollama` de esta máquina devuelve `datetime`; la ficha
imprime la ruta con separador de Windows). Después se agregó el test del PDF vigente (verde junto con los del PDF:
23 passed).

## Fase 29 — N9: límite de ejecución propio y regla de pares de productos

**Problema (Fase 28):** con llama3.1:8b, los 3 intentos de N9 se cortaron por el timeout de 10 s de la guarda.
Se pidió subir el límite solo para N9 (20-25 s) u optimizar la consulta de referencia, y confirmarlo con una
corrida real en la que el modelo genere la consulta.

### 1. Medición antes de cambiar nada

En la conexión de la guarda del chat (`abrir_conexion_solo_lectura`: 12 hilos, `memory_limit` por defecto de
25 GiB), sin límite de tiempo:

| Consulta | Tiempo |
|---|---|
| Referencia de N9 (con la poda de ≥10,000 pedidos) | **1.4 s** (los 6.9 s de la Fase 28 eran con `threads=1`, la conexión de la capa analítica) |
| La que escribió llama (une `products` dos veces y agrupa por `product_name`, pares por `add_to_cart_order`) | **Out of Memory a los 63.5 s** (24.9/25.0 GiB) |
| Self-join sin poda, agrupando primero por `product_id` | **10.4 s** |

Conclusión: **ni subir el límite ni optimizar la referencia bastaban.** La referencia ya era rápida en esa
conexión. La consulta del modelo no termina con ningún límite, porque se queda sin memoria. Un índice o una tabla
auxiliar de conteos por producto acelerarían la referencia, pero no la consulta que escribe el modelo. Lo que
la hacía inviable era su **forma**: agrupar por nombres después del doble JOIN.

### 2. Cambios

- **Límite propio por necesidad:** `DefNecesidad.timeout_consulta_s`, con None = el del chat (10 s). N9 =
  **25 s**. `iteraciones_juez.timeout_consulta(nid)` lo resuelve, y lo usan el panel de iteraciones y la
  entrega de IA (`formato_entrega`) a través del nuevo `responder_pregunta_instacart(..., timeout_consulta_s=)`,
  que llega hasta `ejecutar_con_guardas` (también en el reintento del revisor). El chat libre y las otras 9
  necesidades siguen en 10 s. El informe de cada corrida muestra el límite usado, y el caption del panel lo
  menciona.
- **Pista de lentitud (`PISTA_TIMEOUT`):** antes decía «evita subconsultas correlacionadas», que aquí no era la
  causa. Ahora también dice: «si un JOIN multiplica filas (p. ej. order_products consigo misma), agrupa primero
  por los ids y une products al final». Se activa también ante «Out of Memory», en la retroalimentación y en
  la bitácora. Mide 200 caracteres, así que cabe entera en una lección de la bitácora.
- **Regla en `REGLAS_DE_NEGOCIO`:** `order_products` tiene ~33.8 M filas. Para pares de productos del mismo
  pedido, la regla indica agrupar primero por los dos `product_id` (`a.product_id < b.product_id`) y unir
  `products` solo al final, y prohíbe agrupar por `product_name` o emparejar con `add_to_cart_order`. Trae una
  plantilla, igual que las reglas existentes de tasa de recompra y pasillos. La plantilla:
  - es una sola sentencia `SELECT` que pasa la guarda;
  - no lleva la poda de la referencia ni ningún valor de la respuesta (test);
  - tarda 7.4-8.6 s en la conexión de la guarda.
- **N9.json regenerado:** la limitación «~7 s, cerca del límite de 10 s» ya no era cierta. Ahora dice que el
  self-join es pesado y que su límite es de 25 s cuando lo genera un modelo.

### 3. Corridas reales con llama3.1:8b

N9, autocorrección, árbitro chequeo, 3 repeticiones × 3 intentos, **sin corte** (para tener 9 consultas
generadas por brazo). Evidencia en `docs/evidencia/fase29/brazo_a/` y `brazo_b/`.

| Brazo | Timeouts | Out of Memory | Otros errores de SQL | Ejecutaron | Correctas |
|---|---|---|---|---|---|
| A: límite de 25 s + pista nueva (sin regla) | **7/9** | 0 | 2 (Binder, Parser) | 0/9 | 0/9 |
| B: + regla de pares | **0/9** | 0 | 1 (Binder: alias `pares`) | **8/9** | **2/9** |

- **Brazo A:** las 7 consultas que se cortaron agrupaban por `p1.product_name, p2.product_name`. Llama **no
  adoptó la pista** entre intentos: la recibió 6 veces y repitió la misma forma.
- **Brazo B:** las 8 consultas que ejecutaron agrupaban por `a.product_id, b.product_id`. Medidas de nuevo en
  la conexión de la guarda tardan **7.1-10.8 s**, y 2 de ellas pasan de 10 s, así que **también hace falta el
  límite de 25 s**: con 10 s, la regla sola habría dejado algunos timeouts.
- **Brazo B, consultas que ejecutaron pero fallaron** (6): ya son errores del modelo, no de tiempo.
  - 1: quitó el `ORDER BY` externo y el top 2 salió desordenado (el par correcto quedó en la fila 2).
  - 3: sin ningún `ORDER BY`.
  - 2: usó `order_products__prior`/`__train` en vez de la vista `order_products`.

**Confirmación:** con el modelo generando la consulta, el timeout **ya no se activa** (0/9 en el brazo B,
frente a 7/9 en el A). Hacen falta las dos piezas: la regla, para que la consulta sea factible, y los 25 s, para
que la consulta factible no se corte.

**Costo de la regla:** la plantilla está en el prompt de todas las preguntas, incluido el chat libre. Para N9
le da al modelo el enfoque (sin la poda ni la respuesta), igual que la regla de tasa de recompra hace con
N3/N4. Por eso N9 mide ya más «seguir la plantilla» que «descubrir el self-join».


### 4. Verificación

- `tests/test_fase29_timeout_n9.py` tiene 8 tests:
  - solo N9 tiene límite propio;
  - el panel y la entrega pasan el límite de la necesidad;
  - el límite llega hasta la guarda y el chat no lo pasa;
  - la guarda real corta con el límite pedido;
  - la pista nombra el JOIN que multiplica filas y cubre «Out of Memory»;
  - la plantilla de la regla agrupa por ids, pasa la guarda y no contiene valores de la respuesta.
- `python -m pytest -q` (completo): `2 failed, 588 passed, 2 xpassed in 156.55s`. Son las 2 fallas del entorno de
  siempre (`test_modelos_cargados_lee_api_ps`, `test_construir_ficha_coincide_con_dataset_instacart_md`).
- El PDF quedó rancio por el cambio de la limitación de N9: `pdf_vigente` lo detectó (la comprobación de
  contenido de la Fase 28). Se regeneró y vuelve a estar vigente.
