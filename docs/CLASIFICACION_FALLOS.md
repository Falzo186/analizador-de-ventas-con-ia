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
