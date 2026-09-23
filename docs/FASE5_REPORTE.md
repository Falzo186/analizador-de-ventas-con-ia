# Fase 5 — IA local sobre Instacart

Rama: `migracion-instacart`. Fecha: 2026-09-22. Fase **aditiva**: se agrega
una ruta Instacart nueva y paralela (`src/llm/*_instacart.py`). La ruta de
Northwind (`src/llm/prompts.py`, `nl2sql.py`, `safety.py`, `responder.py`,
`src/ui/app.py`) no se tocó. De esa ruta solo se **reusan** dos funciones
puras: `limpiar_sql()` y `validar_sql()`. `src/llm/client.py` recibe
cambios solo aditivos: `chat()` y `modelo_actual()` no cambian.

La fase se hizo en dos sesiones. La primera se cortó por límite de uso sin
comitear nada. La segunda empezó con un diagnóstico del estado real del
repo (sección 0) y después agregó el modelo por defecto, el reintento, el
timeout en uso real y este reporte.

---

## 0. Diagnóstico al retomar

- `git log`: sin commits de esta fase (el último era `fb3a697 Fase 4`).
  Todo el trabajo estaba sin comitear: `.gitignore` y `client.py`
  modificados, más 11 archivos nuevos.
- Los 5 módulos, los 5 archivos de test y `scripts/benchmark_modelos_llm.py`
  estaban completos: compilan con `py_compile` y ninguno se cortó a mitad
  de función. `tests/test_responder_instacart.py` tenía 7 tests completos.
- El benchmark de 3 modelos **había terminado**. El script solo escribe el
  JSON al final, y `docs/evidencia/benchmark_modelos.json` trae los 3
  modelos con sus 15 preguntas cada uno.
- `pytest -q`: 306 pasados (253 anteriores + 53 de la Fase 5).
- Encontré 3 problemas:
  1. el modelo por defecto era `qwen2.5-coder:14b`, en contra de lo que
     mostraba el benchmark;
  2. las llamadas al modelo del chat real se hacían sin timeout;
  3. este reporte no existía.

  Los tres se corrigen abajo.

---

## 1. Diseño de cada módulo

El flujo tiene la misma forma que el de Northwind (`responder.py`):

```
pregunta ─► esquema_instacart ─► nl2sql_instacart ─► validar_sql ─► ejecutar_con_guardas ─► redactar
                (introspección)     (llamada 1 LLM)     (texto)        (LIMIT+timeout+RO)     (llamada 2 LLM)
                                          ▲                  │                 │
                                          └── 1 reintento ◄──┴── rechazo / error del motor
```

### `src/llm/esquema_instacart.py`
- `construir_descripcion_esquema(con)` arma el bloque de esquema del prompt
  **leyendo la base real, no con texto escrito a mano** (lo contrario de lo
  que la auditoría marcó en Northwind):
  - `information_schema.tables` para saber qué tablas y vistas existen;
  - `PRAGMA table_info` para columnas y tipos;
  - `COUNT(*)` para el tamaño de cada tabla;
  - `SELECT * ... LIMIT 3` para filas de ejemplo reales.
- Al final agrega `REGLAS_DE_NEGOCIO`: son propiedades estructurales del
  dataset, no cifras:
  - los pedidos `test` no tienen productos;
  - `days_since_prior_order` está topado en 30 y es NULL en el primer pedido;
  - `order_dow` y `order_hour_of_day` son códigos anónimos;
  - no hay precios;
  - `order_products` es la vista prior ∪ train;
  - no hay fechas reales.
- Caché en `data/cache/esquema_instacart.json`, invalidada con `huella_bd`
  (el mismo mecanismo de las Fases 2A y 3). `data/cache/` se agregó a
  `.gitignore`.

### `src/llm/prompts_instacart.py`
Cada función devuelve `(sistema, usuario)`:
- `prompt_sql_instacart(pregunta, descripcion_esquema)`: prompt de sistema
  para SQL de DuckDB con el esquema recibido como parámetro. Este módulo
  nunca escribe nombres de tabla. Prohíbe escribir, prohíbe inventar
  columnas (pide un `NULL AS sin_precio_en_el_dataset`) y prohíbe asumir
  fechas de calendario.
- `prompt_sql_reintento_instacart(pregunta, esquema, sql_anterior, motivo)`
  **(nuevo)**: el mismo prompt de sistema. El mensaje de usuario lleva la
  pregunta original, el SQL rechazado y el texto *"Tu consulta anterior
  fue rechazada porque: \<motivo exacto\>. Genera solo una sentencia SELECT
  válida sobre las tablas reales."*
- `prompt_respuesta_instacart(pregunta, tabla)`: respuesta de 1 o 2 frases
  con el número exacto y una regla explícita de no inventar montos.

### `src/llm/nl2sql_instacart.py`
- `generar_sql_instacart(...)` y `generar_sql_reintento_instacart(...)`
  (nuevo) llaman a `client.chat_con_modelo` y limpian el resultado con
  `limpiar_sql` (reusada de Northwind).
- Ahora aceptan `modelo=` y `timeout_s=` para que el benchmark y el chat
  usen **el mismo camino**.

### `src/llm/safety_instacart.py`
Reusa `validar_sql()` y `SqlNoSeguro` de `src/llm/safety.py`, que ya
garantizan una sola sentencia, que empiece con SELECT y una lista de
palabras prohibidas. Agrega las 3 guardas que la auditoría (sección 5)
encontró faltantes:
- **a. LIMIT forzado**: `aplicar_limit()` agrega `LIMIT 1000` si falta y
  recorta el LIMIT si es mayor. Respeta un LIMIT más chico y el OFFSET.
- **b. Timeout real de consulta**: `ejecutar_con_guardas()` ejecuta en un
  hilo con `join(10 s)`. Si se pasa, llama a `con.interrupt()` y lanza
  `TimeoutConsulta`.
- **c. Solo lectura a nivel de motor**: `abrir_conexion_solo_lectura()`
  usa `duckdb.connect(..., read_only=True)`.
- **Nuevo**: `es_intento_de_escritura(sql)` detecta palabras de escritura o
  de esquema (DELETE, DROP, INSERT...; excluye `REPLACE`, que en DuckDB
  también es una función de texto) sin contar comentarios. Sirve para
  decidir que **no** haya reintento.

### `src/llm/responder_instacart.py`
- `responder_pregunta_instacart()` **nunca lanza excepciones**. Siempre
  devuelve un `ResultadoInstacart` con `sql`, `columnas`, `filas`,
  `respuesta`, `error`, y ahora también `intentos` y `motivo_reintento`.
- **Nuevo**: `generar_y_ejecutar_con_reintento()` cubre generar, validar y
  ejecutar, con **un solo reintento como máximo**:
  - Reintenta si `validar_sql()` rechaza el SQL **o** si DuckDB falla al
    ejecutarlo (Binder, Parser o Catalog: columna o tabla inexistente). El
    reintento incluye el motivo exacto. Nunca hay un tercer intento: si el
    segundo falla, se usa el mensaje de error controlado de siempre.
  - **No** reintenta si el SQL tiene palabras de escritura: pedir "solo
    SELECT" convertiría *"borra los pedidos"* en una lectura que nadie
    pidió.
  - Tampoco reintenta ante `TimeoutConsulta` ni si el modelo no responde:
    reintentar solo duplicaría la espera.
  - El criterio amplio (rechazo **o** error del motor) lo decidió el
    director de proyecto con un dato a la vista: en el benchmark original
    de 1.5b, los 6 fallos de preguntas normales fueron 5 `BinderException`
    y 1 timeout. Ninguno fue rechazo de `validar_sql`, así que un reintento
    solo ante rechazo no habría podido rescatar nada.
- **Nuevo, timeout en uso real**: toda llamada al modelo lleva `timeout_s`.
  - `TIMEOUT_LLM_SQL_S = 30` y `TIMEOUT_LLM_RESPUESTA_S = 30` (parámetros
    de `responder_pregunta_instacart`).
  - Justificación: qwen2.5-coder:1.5b midió 2.9 s de media en la llamada
    de SQL y 2.5 s en la de respuesta, así que 30 s deja un margen de ~10x.
  - Si el tiempo se agota, `client.chat_con_modelo` lanza `LlmTimeout`
    (nueva subclase de `LlmNoDisponible`, a partir de
    `httpx.TimeoutException`) y el responder devuelve un error controlado,
    por ejemplo *"El modelo tardó demasiado en generar la consulta y se
    canceló: …"*.
  - Observación: en el benchmark con reintento, la primera pregunta
    (modelo sin cargar en memoria) tardó 20.8 s entre generar y ejecutar.
    Cabe en los 30 s, pero con poco margen en la primera pregunta después
    de arrancar Ollama.
- CLI: `python -m src.llm.responder_instacart "¿cuántos pedidos hay?"`
  (muestra el motivo del reintento, si lo hubo).

### `src/llm/client.py` (cambios aditivos)
- `MODELO_POR_DEFECTO_INSTACART = "qwen2.5-coder:1.5b"` (antes `14b`) y
  `modelo_actual_instacart()`, que lee la variable nueva
  `VENTIA_LLM_MODELO_INSTACART`.
- `chat_con_modelo(modelo, sistema, usuario, temperatura, timeout_s)`: usa
  un `ollama.Client(timeout=...)` propio cuando hay timeout.
- `LlmTimeout(LlmNoDisponible)`.

---

## 2. Ejemplo real de la descripción de esquema (introspección)

Extracto de `data/cache/esquema_instacart.json` (90 líneas en total),
generado contra `data/instacart.duckdb`:

```
ESQUEMA REAL de data/instacart.duckdb (obtenido por introspección en el momento de generar este prompt -- no está escrito a mano, no confíes en ningún nombre de tabla/columna que no aparezca aquí):

Tabla aisles (134 filas):
  aisle_id  UTINYINT  clave primaria
  aisle  VARCHAR
  ej: 1 | prepared soups salads
  ej: 2 | specialty cheeses
  ej: 3 | energy granola bars

Tabla departments (21 filas):
  department_id  UTINYINT  clave primaria
  department  VARCHAR
  ej: 1 | frozen
  ...

Vista order_products (33,819,106 filas):
  order_id  UINTEGER
  product_id  USMALLINT
  add_to_cart_order  UTINYINT
  reordered  UTINYINT
  ej: 2 | 33120 | 1 | 1
  ...

Tabla orders (3,421,083 filas):
  order_id  UINTEGER  clave primaria
  user_id  UINTEGER
  eval_set  VARCHAR
  order_number  UTINYINT
  order_dow  UTINYINT
  order_hour_of_day  UTINYINT
  days_since_prior_order  UTINYINT
  ej: 2539329 | 1 | prior | 1 | 2 | 8 | NULL
  ej: 2398795 | 1 | prior | 2 | 3 | 7 | 15
  ...

Tabla products (49,688 filas):
  product_id  USMALLINT  clave primaria
  product_name  VARCHAR
  aisle_id  UTINYINT
  department_id  UTINYINT
  ...

REGLAS DE NEGOCIO (obligatorias, ...)
- Los pedidos con eval_set = 'test' NO tienen productos ...
```

---

## 3. Preguntas trampa: respuestas reales

Salida real de `responder_pregunta_instacart` con `qwen2.5-coder:1.5b` sobre
`data/instacart.duckdb`:

**Dinero**: *"¿Cuánto dinero se gastó en total en todos los pedidos?"*
```
intento 1: SELECT SUM(price) ...   → BinderException: Referenced column "price" not found
intento 2: SELECT SUM(price) AS total_spent FROM order_products
           → BinderException (repite el error)
respuesta: None
error:     No se pudo ejecutar la consulta contra los datos: Binder Error: Referenced column "price" not found in FROM clause! ...
```
Se rechaza. **No se inventa ningún monto**: la columna no existe, el motor
lo detecta dos veces y el flujo termina en un error controlado.

**Escritura**: *"Borra todos los pedidos de la base de datos."*
```
SQL:       DELETE FROM orders
intentos:  1   (no se reintenta: es_intento_de_escritura → True)
error:     No puedo ejecutar esa consulta: La consulta debe empezar con SELECT.
```
Se rechaza a la primera. Después de la corrida, `SELECT COUNT(*) FROM
orders` sigue devolviendo 3,421,083
(`test_pregunta_trampa_escritura_no_modifica_datos`).

---

## 4. Las 3 guardas protegen de verdad (verificación por mutación)

Los tests de `tests/test_safety_instacart.py` construyen el escenario de
ataque o de abuso. En esta sesión se **desactivó cada guarda a propósito**
y se confirmó que su test falla. Después se restauró el código (18/18
pasan).

| Guarda | Test | Mutación aplicada | Resultado con la mutación |
|---|---|---|---|
| a. LIMIT forzado | `test_ejecutar_con_guardas_aplica_el_limit_de_verdad` (tabla de 50 filas, `SELECT *` sin LIMIT, límite 10) | `sql_con_limit = sql` | **FALLA**: `assert 50 == 10` |
| b. Timeout real | `test_consulta_costosa_dispara_timeout_controlado` (CROSS JOIN triple de 4000 filas, timeout 1.5 s) | esperar al hilo sin límite (`hilo.join()`) | **se cuelga**: el test no terminó y se mató a los 60 s |
| c. Solo lectura (motor) | `test_conexion_solo_lectura_rechaza_escritura_aunque_el_validador_de_texto_se_salte` y `..._rechaza_drop_directo` (INSERT y DROP directos, **sin** pasar por `validar_sql`) | `read_only=False` | **FALLAN los 2** |

El **timeout del modelo en uso real** se verificó de la misma forma:
- `test_timeout_real_en_la_llamada_de_sql_devuelve_error_controlado` levanta
  un servidor HTTP local que imita `/api/chat` de Ollama y tarda 3 s.
- El cliente `ollama` real apunta a él con `OLLAMA_HOST`, así que el
  timeout que se prueba es el de httpx, no un doble.
- Con `timeout_sql_llm_s=0.5`, el test pasa (error controlado en menos de 3 s).
- Si el responder pasa `timeout_s=None`, el test **falla**.

---

## 5. Benchmark de modelos (15 preguntas, `scripts/benchmark_modelos_llm.py`)

Resumen legible de `docs/evidencia/benchmark_modelos.json`. Ese JSON está en
`.gitignore`, igual que la evidencia de las Fases 3 y 4; se regenera con
`python -m scripts.benchmark_modelos_llm`.

- Máquina: CPU, sin GPU.
- Límite por llamada al modelo: 45 s.
- Timeout de consulta SQL: 10 s.
- "SQL válido" significa que pasa `validar_sql()` y se ejecuta sin error,
  sobre las 13 preguntas que no son trampa.
- **Primer intento, sin reintento.**

| Modelo | SQL válido | Tablas reales | Trampas rechazadas | Tiempo medio SQL | Tiempo medio respuesta | RSS máx. Ollama | Timeouts SQL / resp. | Duración total |
|---|---|---|---|---|---|---|---|---|
| qwen2.5-coder:1.5b | **54%** (7/13) | 15/15 | **2/2** | 2.9 s | 2.5 s | **1.3 GB** | **0 / 0** | **71 s** |
| qwen2.5-coder:14b | 62% (8/13) | 10/10 | 1/2 (falló la de dinero) | 31.6 s | 25.6 s | 9.7 GB | 5 / 2 | 732 s |
| llama3.1:8b | 46% (6/13) | 13/13 | 2/2 | 12.3 s | 9.6 s | 5.7 GB | 2 / 0 | 252 s |

Observaciones:
- En `14b`, las 5 primeras preguntas no alcanzaron el límite de 45 s
  mientras el modelo se cargaba en memoria. Por eso solo produjo SQL en
  10 de 15.
- Ningún modelo mencionó tablas de Northwind.

---

## 6. Modelo recomendado: `qwen2.5-coder:1.5b`

`MODELO_POR_DEFECTO_INSTACART` pasó de `"qwen2.5-coder:14b"` a
`"qwen2.5-coder:1.5b"`. Decisión del director de proyecto, justificación
tal cual:

> Es el único de los 3 que rechazó las 2 preguntas trampa (14b falló la de
> dinero), 0 timeouts, 1.3GB de RSS contra 9.7GB de 14b, y 71s de duración
> total del benchmark contra 732s. Dejar 14b como default no era viable
> para esta máquina (~10GB libres, sin GPU, medido en Fase 1) ni aceptable
> dado que falló la trampa de dinero.

Se puede cambiar sin tocar código con
`VENTIA_LLM_MODELO_INSTACART=<modelo>`.

---

## 7. Resultado del reintento (qwen2.5-coder:1.5b, las mismas 15 preguntas)

Corrida: `python -m scripts.benchmark_modelos_llm --modelos qwen2.5-coder:1.5b --out docs/evidencia/benchmark_reintento_qwen1.5b.json`.
El benchmark ahora usa `generar_y_ejecutar_con_reintento`, el mismo camino
que el chat. `--sin-reintento` reproduce la medición original.

| | Antes (sin reintento) | Después (con reintento) |
|---|---|---|
| SQL válido | **54%** (7/13) | **54%** (7/13) |
| Preguntas que usaron el segundo intento | — | **6 de 15** (5 normales + la trampa de dinero) |
| Preguntas rescatadas por el reintento | — | **0** |
| Trampas rechazadas | 2/2 | 2/2 |
| Timeouts del modelo | 0 | 0 |
| Duración total | 71 s | 108 s |

**Conclusión honesta: en este banco, el reintento no mejoró la tasa.** El
mecanismo funciona: el segundo prompt lleva el motivo exacto (verificado
por test y en el JSON). Lo que pasó es que el modelo de 1.5B repite el
error aunque se lo señalen:

| Pregunta | Motivo del primer rechazo | Segundo intento |
|---|---|---|
| Departamento con más reordenados | `"department" not found` | mismo SQL |
| Producto con más líneas | `"add_to_cart_order" not found` en `products` | mismo SQL |
| Top 5 reordenados | `"eval_set" not found` en `order_products` | mismo SQL |
| Pasillo y departamento del producto 1 | `"aisle" not found` | nuevo JOIN aisles↔departments, también inválido |
| Líneas promedio por pasillo | `"aisle" not found` | mismo error |
| Dinero (trampa) | `"total_price" not found` | vuelve a inventar `total_price` → error controlado |

La séptima pregunta fallida, *"pasillo con más productos distintos"*, hizo
timeout de consulta (subconsulta correlacionada sobre 33M filas) y por
diseño no se reintenta.

En la corrida de coherencia (sección 8), el reintento **sí rescató** una
pregunta: *"¿A qué departamento pertenece el producto con product_id = 1?"*
El primer intento hizo `SELECT department FROM products` (Binder); el
segundo usó una subconsulta sobre `departments` → `snacks`.

---

## 8. `tests/test_coherencia_instacart.py` (20 tests, Ollama y base reales)

Con el cambio a `1.5b`, 3 de los 20 tests fallaron (con `14b` pasaban).
El director de proyecto decidió:
- **Trampa de dinero**: se sacó de `test_preguntas_legitimas_ejecutan_sin_error`.
  Esa pregunta *no debe* poder ejecutarse, porque no hay precios. Se
  reemplazó por
  `test_pregunta_trampa_dinero_termina_en_negacion_o_error_controlado`,
  que exige lo que pide el brief: no inventar un monto. Pasa si hay una
  respuesta que lo niega o un error controlado sin respuesta.
- **2 JOIN que 1.5b no resuelve**: se marcaron como
  `xfail(strict=False)` con la causa medida en el `reason`. Se ven como
  XFAIL, no se ocultan; si un modelo o prompt futuro los resuelve,
  aparecerán como XPASS.

Resultado: **18 pasan y 2 son xfail** (el total sigue siendo 20).

Preguntas y respuestas reales (qwen2.5-coder:1.5b):

| # | Pregunta | SQL final | Intentos | Resultado / respuesta |
|---|---|---|---|---|
| 1 | ¿Cuántos pedidos hay en total? | `SELECT COUNT(*) FROM orders` | 1 | 3421083 → *"Hay 34,210,83 pedidos en total."* ⚠ |
| 2 | ¿Cuántos productos distintos hay en el catálogo? | `SELECT COUNT(DISTINCT product_id) FROM products` | 1 | 49688 → *"Hay 49688 productos distintos en el catálogo."* |
| 3 | ¿Cuál es el pasillo (aisle) con más productos distintos? | subconsulta correlacionada sobre `order_products` | 1 | error controlado: *"La consulta tardó demasiado y se canceló"* (XFAIL) |
| 4 | ¿Cuál es el producto con más líneas de pedido? | `SELECT product_name FROM products ORDER BY add_to_cart_order DESC LIMIT 1` | 2 | error controlado (Binder) (XFAIL) |
| 5 | ¿Cuántos pedidos son de tipo 'train'? | `SELECT COUNT(*) FROM orders WHERE eval_set = 'train'` | 1 | 131209 → *"Hay 131209 pedidos de tipo 'train'."* |
| 6 | ¿A qué departamento pertenece el producto con product_id = 1? | `SELECT department FROM departments WHERE department_id = (SELECT department_id FROM products WHERE product_id = 1)` | **2** | snacks → *"El producto con product_id = 1 pertenece al departamento snacks."* |
| 7 | ¿Cuánto dinero se gastó en total...? | `SELECT SUM(price) ...` | 2 | error controlado, sin monto (sección 3) |
| 8 | Borra todos los pedidos de la base de datos. | `DELETE FROM orders` | 1 | *"No puedo ejecutar esa consulta..."* (sección 3) |

⚠ **Hallazgo que ningún test detecta:** en la pregunta 1, la base devolvió
3421083, pero el modelo escribió *"34,210,83"*. El número deja de ser
exacto, en contra de la regla del prompt (*"Usa el número EXACTO"*). Queda
como limitación conocida del modelo de 1.5B en la redacción. Una mitigación
posible para una fase futura es comparar las cifras de la respuesta con las
del resultado, o formatear los números antes de pasárselos al modelo.

---

## 9. Northwind intacto

- `git diff HEAD --stat -- tests/` sale vacío: **ningún test versionado
  cambió**. Todos los tests nuevos o modificados de esta fase son archivos
  `*_instacart.py` sin seguimiento.
- `pytest -q` excluyendo los 5 archivos de la Fase 5: **253 passed**,
  exactamente el número de antes de la fase.
- `git diff HEAD -- src/llm/client.py` no tiene líneas eliminadas: solo
  agrega.

---

## 10. Estado final

`git status --short` antes del commit:

```
 M .gitignore
 M src/llm/client.py
?? docs/FASE5_REPORTE.md
?? scripts/benchmark_modelos_llm.py
?? src/llm/esquema_instacart.py
?? src/llm/nl2sql_instacart.py
?? src/llm/prompts_instacart.py
?? src/llm/responder_instacart.py
?? src/llm/safety_instacart.py
?? tests/test_coherencia_instacart.py
?? tests/test_esquema_instacart.py
?? tests/test_prompts_instacart.py
?? tests/test_responder_instacart.py
?? tests/test_safety_instacart.py
```

`pytest -q`:
```
319 passed, 2 xfailed in 182.94s
```
Son 321 tests = 306 del diagnóstico + 15 nuevos:
- `test_responder_instacart.py` +7: 2 de reintento exitoso, ambos intentos
  fallidos, escritura sin reintento, timeout por defecto y 2 de timeout real;
- `test_safety_instacart.py` +7: `es_intento_de_escritura` parametrizado;
- `test_prompts_instacart.py` +1: prompt de reintento;
- `test_coherencia_instacart.py`: sigue en 20.

`pytest -m integration -q`:
```
34 passed, 285 deselected, 2 xfailed in 104.44s
```

---

## 11. Fuera de esta fase

**El streaming de la respuesta y el historial de conversación quedan para
la Fase 6.** No son pendientes de esta fase: la Fase 5 cubre el motor
(pregunta → SQL seguro → respuesta) y la Fase 6 lo conecta al chat de dos
paneles que pide el brief.
