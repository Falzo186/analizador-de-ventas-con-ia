# HOTFIX 1 (sobre la Fase 8): navegación "que deja de responder" y Binder Error de `eval_set`

Rama: `migracion-instacart`. Fecha: 2026-09-23. Base: `a88911d` (Fase 8).

**Resumen**:

| Síntoma reportado | Causa raíz (con evidencia) | ¿Regresión de la Fase 8? |
|---|---|---|
| Al seleccionar N1 y alternar 💬/📊, "el botón deja de responder" | Con una pregunta **en proceso**, cada cambio de vista abandona una ejecución cuyo hilo **completa igual** la fase SQL (Ollama + DuckDB) y descarta el resultado. Cada regreso al chat lanza otro cálculo completo. Los hilos se acumulan en la cola de Ollama: 1 pregunta → **8 llamadas** a Ollama, la vista tarda 2-3,6 s en cambiar y la respuesta ~41 s en llegar. | **Sí, de la Fase 8**: es su diseño de "reprocesar al volver", que la Fase 8 midió solo con preguntas rápidas. |
| `Binder Error: Values list "op" does not have a column named "eval_set"` | **No es N1 ni `src/analytics`**. Es el SQL que **el modelo del chat** generó para la pregunta del usuario. Lo induce el texto de una regla del prompt (`REGLAS_DE_NEGOCIO`): "filtra explícitamente eval_set IN ('prior','train') **o** usa la vista order_products", y el modelo combinó las dos cosas. | **No.** El mismo tipo de error ya estaba documentado en la Fase 5 (benchmark, "top 5 reordenados": `eval_set not found`). |
| Pregunta duplicada dos veces con el mismo error | **No se reproduce** en ninguno de los 6 escenarios probados (sección 3). Ninguna ruta del código agrega un turno dos veces. | No demostrado (sección 3). |

---

## 1. Reproducción

### 1.1 Método
- **AppTest** con Ollama y `data/instacart.duckdb` reales.
- **Navegador real**: Firefox 156 headless con Selenium, instalado en un
  venv **aparte** en el scratchpad, sin tocar las dependencias del
  proyecto. Corre contra `streamlit run src/ui/app_instacart.py` real.
- **Instrumentación del servidor sin modificar la app**: un envoltorio en
  el scratchpad (`app_debug.py`) importa `app_instacart`, registra cada
  ejecución (inicio, fin o `StopException`), `responder_en_vivo` y
  `responder_pregunta_instacart`, y llama a `main()`.
- **Proxy TCP delante de Ollama**, el de la Fase 8, que cuenta cada
  `POST /api/chat`.

### 1.2 Contexto: tu servidor
`ps` muestra tu proceso: `PID 119499 … streamlit run src/ui/app_instacart.py`,
**iniciado el 23-sep a las 09:23:06**.

| Momento | Qué pasó |
|---|---|
| 09:04 | Commit de la Fase 7 |
| 09:23 | Arranca tu servidor, con el código de la Fase 7 |
| 09:36-09:42 | Se edita `app_instacart.py` para la Fase 8 |
| 09:50 | Commit de la Fase 8 |

Tu pestaña vivió el cambio de código con el servidor corriendo. Su consola
va a tu terminal, así que **no pude leer ese log**. Se reprodujo también
esa transición (1.3.b).

### 1.3 Resultados

**a) Tu secuencia exacta, esperando la respuesta** (pregunta → fijado N1 →
💬 → 📊 → 💬 → fijado N5 → 💬), con servidor nuevo, código de la Fase 8 y
navegador real. **No falla**: cada clic cambia la vista, 0 excepciones,
siempre 2 mensajes en el chat y el log del servidor sin errores. El Binder
Error aparece como `st.error` controlado, "⚠️ No se pudo responder…", en la
burbuja de la respuesta.

**b) Transición de código como la tuya**: sesión abierta con el código de
la Fase 7 (`git show 57d3a86:src/ui/app_instacart.py`), pregunta
respondida, cambio del archivo a la Fase 8 con el servidor corriendo, clic
en "Rerun" del aviso *Source file changed*, y luego N1 → 💬 → 📊 → 💬.
**No falla**: 2 mensajes, 0 excepciones, log limpio. El archivo quedó igual
a `HEAD` después de la prueba.

**c) La variante que sí reproduce el síntoma.** Tu pregunta toma el camino
lento (SQL → Binder → reintento → Binder, ~8-15 s). Si se hace clic en N1 y
se alterna 💬/📊 **mientras la respuesta sigue en proceso**:
```
[ 7.7s] >>> pregunta enviada
[10.3s] >>> clic N1           → dashboard
[17.3s] >>> clic 💬           → chat "Generando…"
[23.4s] >>> clic 📊
[25.5s]  tras clic 📊: h1=[] mensajes=['¿Qué productos…', 'Generando…'] status=''   ← el servidor ya dibujó el dashboard,
[27.0s]  tras clic 📊 (+1.5 s): h1=['📊 Dashboard Instacart','N1 — Recompra por producto']  ← el navegador lo muestra 2-3,6 s después
[30.0s] >>> clic 💬
[42.8s] >>> clic 💬
[79.4s] QUIETO al final: mensajes=['¿Qué productos…', 'error']            ← la respuesta llega ~35 s después del último clic
```
Registro del servidor (envoltorio) y del proxy, en otra corrida del mismo
escenario:
```
..886.973  responder_pregunta_instacart INICIO      ← cálculo 1 (hilo de la ejecución original)
..889.620  RUN INICIO vista=dashboard / ..890.687 RUN FIN OK
..893.663  responder_pregunta_instacart INICIO      ← cálculo 2 (regreso al chat; el 1 sigue vivo)
..902.994  responder_pregunta_instacart INICIO      ← cálculo 3
..911.180  responder_pregunta_instacart FIN  → responder_en_vivo ABORTADO por StopException   ← trabajo tirado
..912.225  responder_pregunta_instacart INICIO      ← cálculo 4
..919.654  … FIN → ABORTADO por StopException                                                    ← tirado
..945.438  … FIN → ABORTADO por StopException                                                    ← tirado
..953.663  … FIN → responder_en_vivo FIN → RUN FIN OK historial=2                                ← el único que se usa
Proxy: 8 POST /api/chat para UNA pregunta (4 cálculos × 2 intentos de SQL)
```

### 1.4 Causa raíz 1: navegación que "deja de responder"
- Un clic en 💬/📊 o en un fijado es un **rerun completo**. Con
  `runner.fastReruns` (valor por defecto), Streamlit arranca la ejecución
  nueva en otro hilo **sin esperar** a la anterior. La anterior solo se
  detiene en su **siguiente llamada a Streamlit**.
- `responder_pregunta_instacart` (la fase SQL: LLM, validación, DuckDB y
  reintento) **no hace ninguna llamada a Streamlit**. El hilo abandonado la
  completa entera (2 llamadas a Ollama y 2 consultas en este caso), y en
  `estado.update(...)` recibe el `StopException` y **descarta el
  resultado**.
- Con el diseño de la Fase 8 ("la pregunta queda en `pregunta_en_curso` y
  se reprocesa al volver"), **cada regreso al chat lanzaba otro cálculo
  completo en paralelo**. Ollama atiende de a uno, así que los cálculos
  vivos se encolan detrás de los abandonados.
- Resultado medido:
  - **8 llamadas** en vez de 2;
  - respuesta final en ~41 s, cuando un cálculo aislado tarda ~8 s;
  - cambio de vista que el navegador muestra 2-3,6 s tarde mientras hay
    hilos trabajando.

  El usuario ve la vista anterior "congelada", con "Generando…" y
  Streamlit ya detenido. Ese es el "deja de responder".
- **Por qué la Fase 8 no lo detectó**: midió con "¿Cuántos pedidos hay en
  total?" (~0,9 s de SQL). Esa medición mostró la duplicación (1-2
  llamadas), pero no la acumulación que produce una pregunta lenta con
  varios cambios de vista.

### 1.5 Causa raíz 2: Binder Error de `op.eval_set`
- El SQL que falla es el que generó el modelo, visto en `ResultadoInstacart.sql`:
  ```sql
  SELECT p.product_name, AVG(op.add_to_cart_order) …, AVG(op.reordered) …
  FROM products p JOIN order_products op ON p.product_id = op.product_id
  WHERE op.eval_set IN ('prior', 'train')      -- ← la vista order_products no tiene eval_set
  GROUP BY p.product_name HAVING …
  ```
  `op` es el alias que el modelo le dio a la vista `order_products`, que
  solo tiene `order_id, product_id, add_to_cart_order, reordered`.
- **No es N1**:
  - `grep eval_set src/analytics/n1_recompra.py` no devuelve nada;
  - `git log -- src/analytics/n1_recompra.py` muestra un solo commit,
    `e383f1e` (Fase 2A);
  - la Fase 8 (`a88911d`) solo tocó `README.md`,
    `docs/FASE8_REPORTE.md`, `src/ui/app_instacart.py` y
    `tests/test_app_instacart.py`;
  - el panel N1 del dashboard carga sin errores en todas las pruebas.

  La pregunta del usuario coincide con el texto de la necesidad N1, y por
  eso pareció que el error era de N1.
- **Qué lo induce**: la regla del prompt (`src/llm/esquema_instacart.py`,
  Fase 5) decía *"si una pregunta necesita productos, filtra
  explícitamente eval_set IN ('prior','train') **o** usa la vista
  order_products"*. El modelo aplicó las dos cosas a la vez. Comparación
  con el modelo real, mismas preguntas:

  | Regla | Pregunta del usuario | "Top 5 reordenados" | "Pedidos de tipo train" |
  |---|---|---|---|
  | Original | `WHERE op.eval_set IN …` → **Binder** | `FROM order_products WHERE eval_set IN …` → **Binder** | OK |
  | Aclarada | OK (1000 filas) | sin `eval_set`; falla por `product_name` sin JOIN (limitación de JOIN ya conocida) | OK |
- **No es una regresión**: el benchmark de la Fase 5 ya registraba
  `"eval_set" not found` en "Dame el top 5 de productos con más veces
  reordenados" (`docs/FASE5_REPORTE.md`, sección 7).

---

## 2. Correcciones aplicadas (mínimas)

### Corrección 1: `src/ui/app_instacart.py`, reutilizar la fase SQL en vez de repetirla
- Nueva función `_resultado_sql(pregunta, contexto)`, que usa
  `responder_en_vivo` en lugar de llamar directo a
  `ri.responder_pregunta_instacart(...)`.
  - Guarda en `st.session_state["sql_en_curso"]` el trío
    `{pregunta, hilo, res}`. El `res` se escribe **en cuanto termina la
    fase SQL, antes de cualquier llamada a Streamlit**, así que también lo
    guarda un hilo abandonado.
  - Al volver al chat con la misma pregunta:
    - si `res` ya está, se reutiliza (una copia con
      `dataclasses.replace`, para que la redacción no comparta el objeto
      con el hilo abandonado);
    - si otro hilo todavía la calcula, **se espera a ese hilo** (sondeo
      cada 0,2 s) en vez de lanzar otro;
    - solo si el hilo murió sin resultado, o la pregunta es otra, se
      calcula.
  - Al agregar el turno al historial, `sql_en_curso` se vacía.
- Lo que **no** cambió:
  - el streaming, la validación de cifras, las guardas y el
    `ResultadoInstacart`;
  - la redacción: si se abandona, se reanuda al volver. El stream abandonado
    se corta solo (Fase 8, c.2).
- Los dos avisos de la Fase 8 se actualizaron para decir lo que ahora pasa:
  *"la consulta sigue en proceso; la redacción se retoma al volver"*.

**Medición con el mismo escenario (1.3.c) después de la corrección:**
```
..190.577  responder_pregunta_instacart INICIO      ← único cálculo
..198.853  responder_en_vivo INICIO (regreso al chat: ESPERA al hilo, no calcula)
..208.104  responder_pregunta_instacart FIN → los dos hilos abandonados terminan con StopException
..211.658  responder_en_vivo INICIO → ..211.711 FIN   ← reutiliza el resultado: 53 ms
..216.305 / ..221.269  RUN dashboard / chat: 66 ms / 8 ms
Proxy: 2 POST /api/chat (antes 8). Respuesta visible a los 29 s del envío (antes 75-79 s).
```

### Corrección 2: `src/llm/esquema_instacart.py`, regla de `eval_set` y clave del caché
- Regla nueva (reemplaza la anterior):
  > La columna eval_set existe SOLO en la tabla orders. La vista
  > order_products y las tablas order_products__prior/__train NO tienen
  > eval_set: nunca escribas order_products.eval_set ni op.eval_set. […] si
  > la pregunta es sobre PRODUCTOS de los pedidos, usa order_products SIN
  > filtrar por eval_set. Si la pregunta es sobre los PEDIDOS en sí
  > (eval_set, order_dow, order_hour_of_day, order_number,
  > days_since_prior_order, user_id), consulta la tabla orders, que es la
  > única que tiene esas columnas.
- **La segunda frase fue necesaria.** Con la primera versión (sin
  "PRODUCTOS / PEDIDOS"), el benchmark mostró una regresión real: *"¿Cuál
  es el order_dow con más pedidos?"* pasó de OK a `FROM order_products …`
  → Binder. Con el alcance precisado, el benchmark vuelve a ser idéntico
  pregunta por pregunta al de la Fase 5 (tabla abajo).
- **Clave del caché.** `data/cache/esquema_instacart.json` se invalidaba
  **solo** por la huella de la base: cambiar la regla habría seguido
  sirviendo la descripción vieja. Ahora el JSON guarda también
  `"reglas"`, y el caché solo se usa si la huella **y** el texto de las
  reglas coinciden. Un caché anterior al HOTFIX, sin ese campo, se
  reconstruye solo.

**Benchmark de 15 preguntas, qwen2.5-coder:1.5b, con reintento**
(`docs/evidencia/benchmark_hotfix1_qwen1.5b.json`):

| | Antes (Fase 5, `benchmark_reintento_qwen1.5b.json`) | Regla v1 (sin alcance) | **Regla final** |
|---|---|---|---|
| SQL válido | 54% (7/13) | 54% (7/13), pero con otras 7 | **54% (7/13), las mismas 7 que antes** |
| `order_dow` con más pedidos | OK | **FALLO** (regresión) | OK |
| Producto más pedido | FALLO | OK | FALLO (igual que antes) |
| Errores que mencionan `eval_set` | 1 ("top 5") | 0 | **0** |
| Trampas rechazadas | 2/2 | 2/2 | 2/2 |

Tu pregunta con la regla final, en 2 corridas: `ok=True`, `intentos=1`, el
mismo SQL de antes **sin** `WHERE op.eval_set …`.

⚠ **Lo que esta corrección NO resuelve** (limitación ya documentada en el
README: la corrección semántica no está garantizada). En una corrida con
redacción, la respuesta a tu pregunta listó los **mismos** 25 productos
como "consistentes" y como "abandonados". Con esta regla el error de SQL
desaparece, pero la respuesta sigue siendo semánticamente pobre, y
distinguir recompra de abandono exige una consulta que 1.5b no arma solo.
La respuesta determinista correcta es el panel N1 del dashboard.

---

## 3. La pregunta duplicada

**No se reproduce.** Se probaron 6 escenarios: 1.3.a, 1.3.b, 1.3.c (tres
corridas) y el recorrido de las 11 necesidades. En todos, el historial
quedó con **exactamente un turno** por pregunta, y en el registro del
servidor solo **una** ejecución llega a `responder_en_vivo FIN`: las demás
terminan en `StopException` **antes** de `historial.append`.

Revisión del código: un turno solo se agrega al final de
`responder_en_vivo`, después de todas sus llamadas a Streamlit. Un hilo
abandonado siempre se detiene en una de esas llamadas (`estado.update`,
`st.write_stream`, `st.markdown`), antes del `append`. No hay otro lugar
que escriba en el historial.

Dos turnos idénticos requieren **dos procesamientos completos**, es decir,
dos envíos de la pregunta. La explicación más probable, **no demostrada**,
es que la pregunta se envió dos veces mientras la interfaz parecía
congelada por la causa 1. No hay evidencia de un doble render.

---

## 4. Tests de regresión (+17)

| Archivo | Test | Qué fija |
|---|---|---|
| `tests/test_app_instacart.py` | `test_regreso_al_chat_reutiliza_la_fase_sql_ya_calculada` | Con el resultado ya guardado por un hilo abandonado: **0** llamadas de SQL al volver, respuesta mostrada, turno agregado una vez y estado limpio |
| | `test_regreso_al_chat_espera_al_hilo_que_todavia_calcula` | Hilo que tarda 0,6 s: se espera (≥ 0,5 s) y **no** se lanza otro cálculo |
| | `test_hilo_muerto_sin_resultado_o_pregunta_distinta_si_calculan` | Resultado de **otra** pregunta: se calcula una sola vez |
| | `test_cada_fijado_navega_ida_y_vuelta[N1…N11]` (11) | Después de una pregunta: fijado Nk → título "Nk — …" → 💬 (2 mensajes) → 📊 → sigue "Nk — …", sin excepciones |
| `tests/test_esquema_instacart.py` | `test_regla_eval_set_dice_que_solo_existe_en_orders` | La regla dice que `eval_set` está solo en `orders`, y ya no dice "filtra explícitamente eval_set IN" |
| | `test_cache_con_reglas_viejas_se_reconstruye` | Misma huella con otras reglas → se reconstruye |
| | `test_cache_sin_campo_reglas_se_reconstruye` | Caché anterior al HOTFIX → se reconstruye |

**Verificación por mutación**: con los cambios de `src/` guardados en un
`git stash`, los 6 tests de regresión de las correcciones 1 y 2 **fallan**
(6 failed). Con las correcciones, pasan.

---

## 5. Las 11 necesidades navegan bien, ida y vuelta

**Navegador real**, con servidor nuevo y el código final. Para cada Nk:
fijado → 💬 → 📊.
```
OK  N1: fijado->['N1 — Recompra por producto'] | 💬 chat=True | 📊 ->['N1 — Recompra por producto'] | excepciones=0
OK  N2: fijado->['N2 — Canasta: pares de productos'] | … | 📊 ->['N2 — Canasta: pares de productos']
…   (N3 a N9 iguales)
OK  N10: fijado->['N10 — Final del carrito / impulso'] | … | 📊 ->['N10 — Final del carrito / impulso']
OK  N11: fijado->['N11 — Predicción del siguiente pedido'] | … | 📊 ->['N11 — Predicción del siguiente pedido']
TODAS OK      — log del servidor: sin errores
```
Además, tu secuencia (1.3.a) con el código final da 0 `st.error` y 0
excepciones en los 8 pasos: la pregunta ya no produce el Binder Error. Y
los 11 tests `test_cada_fijado_navega_ida_y_vuelta` cubren lo mismo en
AppTest.

---

## 6. Qué tienes que hacer tú

**Reinicia tu servidor** (PID 119499, iniciado a las 09:23). Streamlit solo
recarga automáticamente el script principal, no los módulos de `src/llm/`,
así que ese proceso sigue con la regla vieja en memoria. El caché del
esquema se reconstruye solo al primer uso (cambió su clave).

---

## 7. Estado final

`git status --short` antes del commit:
```
 M src/llm/esquema_instacart.py
 M src/ui/app_instacart.py
 M tests/test_app_instacart.py
 M tests/test_esquema_instacart.py
?? docs/HOTFIX1_REPORTE.md
```

`pytest -q`:
```
326 passed, 2 xfailed in 186.81s
```
Son 328 = 311 anteriores (309 passed + 2 xfailed) + 17 tests de regresión.
Los 2 xfailed son los mismos de la Fase 5.

`pytest -m integration -q`:
```
34 passed, 292 deselected, 2 xfailed in 114.17s
```
