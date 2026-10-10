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
