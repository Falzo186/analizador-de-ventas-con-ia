# Fase 10: entregable de curso, JSON individual por necesidad (software e IA local)

Rama: `migracion-instacart`. Fecha: 2026-09-26. Base: `d681701` (Fase 9),
con el árbol limpio.

Esta fase **no es parte de la migración**: solo agrega una forma de exportar
lo que ya existe, para cumplir la rúbrica ("1 json por necesidad", puntos 3
y 4). Necesidades de la entrega: **N1, N2, N4, N9, N11**. Las otras 6 siguen
iguales en el dashboard y no tienen descarga individual ni JSON de IA.

---

## 1. Diseño

### 1.1 Qué se tocó

Los archivos existentes solo reciben **líneas nuevas**: `git diff --stat`
contra la Fase 9 da 114 inserciones y 0 borrados en `servicio.py`,
`dashboard.py` y `app_instacart.py`. No se modificó `src/analytics`, las
guardas, el streaming, la validación de cifras ni el chat normal.

| Archivo | Cambio |
|---|---|
| `src/dashboard/servicio.py` | + `NECESIDADES_ENTREGA`, `DescargaJson`, `json_para_descarga()` |
| `src/ui/dashboard.py` | + `render_descarga_json()` y su llamada en `render_panel` (solo las 5) |
| `src/llm/formato_entrega.py` | **nuevo**: preguntas fijas, `generar_entrega_ia()`, `empaquetar()`, `a_json()`, CLI |
| `src/ui/app_instacart.py` | + expander «📋 Entrega» en la vista de dashboard (`render_entrega_ia`) |
| `docs/ENTREGA_5_NECESIDADES.md` | **nuevo** (Parte C) |
| `tests/test_fase10_entrega.py` | **nuevo**: 35 tests |

### 1.2 Parte A: JSON determinista

- `servicio.json_para_descarga(nid)` llama primero a
  `servicio.cargar_necesidad(nid)`, el mecanismo de caché de siempre. Si
  `data/referencia/{nid}.json` falta o su `huella_bd` no coincide con la BD,
  lo recalcula y lo reescribe. **Después** lee los bytes del archivo tal
  cual están en disco. No se vuelve a serializar nada, así que lo que se
  descarga es el archivo y nunca uno rancio.
- En `render_panel`, justo debajo del encabezado de las 5 necesidades:
  `st.download_button("⬇️ Descargar JSON de esta necesidad", file_name="{nid}.json")`.
  Si hubo que recalcular, un caption lo dice
  («⚡ `N4.json` faltaba o estaba desactualizado: se recalculó antes de ofrecerlo»).
- Sin `st.cache_data` a propósito: la comprobación de huella cuesta
  ~0.1 s con caché tibia (medido: N1 0.119 s, N11 0.077 s), así que se hace
  en cada ejecución y no puede quedar un archivo viejo en caché de sesión.
- `servicio.py` sigue sin importar streamlit (su test AST sigue pasando).
  `dashboard.py` sigue sin IA: la Parte B vive en `app_instacart.py`.

### 1.3 Parte B: JSON de la IA local

- `formato_entrega.PREGUNTAS_ENTREGA` se arma **importando** la constante
  `PREGUNTA` de `n1_recompra`, `n2_canasta`, `n4_frecuencia`, `n9_tamano`
  y `n11_prediccion`, la misma que cada `calcular()` pone en `r.pregunta`.
  No hay texto escrito a mano.
- `generar_entrega_ia(nid, modelo=…, timeout_*)` llama a
  **`ri.responder_pregunta_instacart(pregunta, redactar=True, …)`**, el
  pipeline de siempre: esquema → SQL → `validar_sql` → `ejecutar_con_guardas`
  (solo lectura, LIMIT, timeout de 10 s) → un reintento → redacción →
  `finalizar_redaccion` (validación de cifras, reintento estricto,
  plantilla). No es un camino paralelo: solo cambia el empaquetado.
- Estructura fija (`CAMPOS_ENTREGA`, en ese orden):
  `necesidad, pregunta, sql_generado, resultado, respuesta_llm, modelo, timestamp`.
  `resultado` es la lista de filas como `{columna: valor}` (NaN/Inf → null;
  fechas/Decimal → texto). `timestamp` es ISO 8601 con zona horaria.
- **Solo se empaqueta una respuesta completa.** Si el pipeline falla (SQL
  rechazado, timeout, Ollama caído…), no hay JSON: la UI muestra el error
  con el mismo estilo del chat (🛡️/⏱️/⚠️) y el SQL, sin botón de descarga.
  Así la estructura queda exacta y no se entrega un JSON a medias.
- UI: expander «📋 Entrega: JSON de la IA para las 5 necesidades» en la vista
  📊 de la app, **arriba** del panel. Adentro: aviso de honestidad, modelo y
  esfuerzo actuales (los mismos del popover ⚙️), y por cada necesidad su
  pregunta, el botón «Generar JSON de IA para Nx», el `st.json` y
  «⬇️ Descargar Nx_ia.json». Los botones se deshabilitan mientras el chat
  responde una pregunta, para no mandar dos consultas a Ollama a la vez.
- Si la validación de cifras reemplazó la redacción (`origen_respuesta`
  «reintento» o «plantilla»), se muestra el mismo aviso 🔢 del chat. El JSON
  no lleva ese dato porque su estructura es fija.

### 1.4 Parte C

`docs/ENTREGA_5_NECESIDADES.md` se generó desde las constantes de
`src.analytics`: **Necesidad** = `DECISION` y **Petición** = `PREGUNTA`, sin
cambios. La única frase nueva es la justificación, y cada tabla o columna
citada se comprobó con `DESCRIBE` sobre `data/instacart.duckdb`. Un test
comprueba que el documento contiene el texto exacto de cada módulo, para
que no se desincronice si alguien cambia una pregunta.

---

## 2. Capturas textuales de los dos flujos (datos y modelo reales)

Hechas con `streamlit.testing.v1.AppTest` sobre `data/instacart.duckdb` y
`qwen2.5-coder:1.5b` real (sin mocks). «Coincide» compara la URL del
`download_button` con el sha224 de (bytes esperados, mime, nombre), que es
como Streamlit identifica el archivo servido: `True` quiere decir que se
descargan exactamente esos bytes.

```text
### Flujo A: dashboard standalone (src/ui/dashboard.py), datos reales

[radio Necesidad = N1]
  st.download_button: «⬇️ Descargar JSON de esta necesidad» -> N1.json (531,282 bytes); coincide byte a byte con data/referencia/N1.json: True
  claves del JSON: ['consultas', 'decision', 'duracion_s', 'fecha_generacion', 'hallazgos', 'huella_bd', 'huella_contenido', 'id', 'limitaciones', 'parametros', 'pregunta', 'tablas', 'tablas_criterio_corte', 'titulo', 'universo', 'valores_clave', 'version_duckdb']
  pregunta: ¿Qué productos se recompran de forma consistente y cuáles se prueban y se abandonan?
  valores_clave[0]: {'consulta_origen': 'global', 'etiqueta': 'Tasa global de recompra', 'unidad': 'fracción', 'valor': 0.5900617242809434}

[radio Necesidad = N11]
  st.download_button: «⬇️ Descargar JSON de esta necesidad» -> N11.json (21,063 bytes); coincide byte a byte con data/referencia/N11.json: True
  claves del JSON: ['consultas', 'decision', 'duracion_s', 'fecha_generacion', 'hallazgos', 'huella_bd', 'huella_contenido', 'id', 'limitaciones', 'parametros', 'pregunta', 'tablas', 'tablas_criterio_corte', 'titulo', 'universo', 'valores_clave', 'version_duckdb']
  pregunta: ¿Qué tan bien predice un baseline determinista (frecuencia+recencia, sin ML) los productos del siguiente pedido?
  valores_clave[0]: {'consulta_origen': 'universo_usuarios', 'etiqueta': 'Usuarios evaluados', 'unidad': 'usuarios', 'valor': 131209}

### Flujo B: app (src/ui/app_instacart.py) -> 📊 -> «📋 Entrega», modelo real

[expander «📋 Entrega: JSON de la IA para las 5 necesidades»]
  st.info: Esta salida es la respuesta **libre** de la IA local a la pregunta fija de cada necesidad: pasa por las mismas guardas del chat (SQL de solo lectura, timeouts, validación de cifras), pero puede tener errores semánticos: un SQL válido que no responde lo que se pregunta (ver README). La referencia determinista es el JSON del software («⬇️ Descargar JSON de esta necesidad» en cada panel).
  st.caption: Modelo: `qwen2.5-coder:1.5b` · esfuerzo Medio (⚙️ arriba para cambiarlo).
  botones: ['Generar JSON de IA para N1', 'Generar JSON de IA para N2', 'Generar JSON de IA para N4', 'Generar JSON de IA para N9', 'Generar JSON de IA para N11']

[clic «Generar JSON de IA para N1»] ok=True origen_respuesta=modelo intentos_sql=1
  st.download_button: «⬇️ Descargar N1_ia.json» (620 bytes); coincide con el st.json mostrado: True

[clic «Generar JSON de IA para N11»] ok=True origen_respuesta=modelo intentos_sql=1
  st.download_button: «⬇️ Descargar N11_ia.json» (600 bytes); coincide con el st.json mostrado: True
```

---

## 3. JSON de la IA generados con el modelo real

`qwen2.5-coder:1.5b` (el modelo por defecto), esfuerzo Medio (30 s por
llamada), desde la UI (flujo B de arriba). Contenido completo:

**N1_ia.json**

```json
{
  "necesidad": "N1",
  "pregunta": "¿Qué productos se recompran de forma consistente y cuáles se prueban y se abandonan?",
  "sql_generado": "SELECT \n    p.product_name,\n    AVG(op.add_to_cart_order) AS avg_add_to_cart_order,\n    AVG(op.reordered) AS avg_reordered\nFROM \n    order_products AS op\nJOIN \n    products AS p ON op.product_id = p.product_id\nGROUP BY \n    p.product_name\nHAVING \n    avg_add_to_cart_order > 1\n    AND avg_reordered > 1",
  "resultado": [],
  "respuesta_llm": "No hay pedidos en esa situación...",
  "modelo": "qwen2.5-coder:1.5b",
  "timestamp": "2026-09-26T12:01:19-06:00"
}
```

**N11_ia.json**

```json
{
  "necesidad": "N11",
  "pregunta": "¿Qué tan bien predice un baseline determinista (frecuencia+recencia, sin ML) los productos del siguiente pedido?",
  "sql_generado": "SELECT \n  AVG(reordered) AS baseline_performance\nFROM \n  order_products\nWHERE \n  order_id = 2539329",
  "resultado": [
    {
      "baseline_performance": 0.0
    }
  ],
  "respuesta_llm": "El resultado de la consulta indica que el baseline determinista (frecuencia+recencia, sin ML) no mejora significativamente los productos del pedido.",
  "modelo": "qwen2.5-coder:1.5b",
  "timestamp": "2026-09-26T12:01:24-06:00"
}
```

**Lectura honesta.** Los dos JSON son la salida real del pipeline y los dos
son **semánticamente incorrectos**: justo el caso que advierte el aviso de
la UI.

- En N1, `AVG(reordered) > 1` nunca se cumple (reordered es 0/1), así que la
  consulta devuelve 0 filas.
- En N11, el modelo no implementa ningún baseline: promedia `reordered` de
  un pedido arbitrario.

Las guardas funcionaron: SQL de solo lectura, sin cifras inventadas. Pero
las guardas no pueden detectar un SQL válido que responde otra cosa. Esta
fase no evalúa exactitud (fuera de alcance), pero para la entrega queda
claro que la referencia es el JSON del software.

### 3.1 Las 5 necesidades con el modelo por defecto (CLI)

Una corrida de `python -m src.llm.formato_entrega Nx --out docs/evidencia/fase10`
por necesidad, con `qwen2.5-coder:1.5b` y 30 s:

| Necesidad | Resultado | Tiempo | Detalle |
|---|---|---|---|
| N1 | JSON ✔ | 9.0 s | mismo SQL que arriba, 0 filas |
| N2 | sin JSON ✘ | 19.9 s | los 2 SQL fallaron al ejecutarse: `Binder Error: Values list "op1" does not have a column named "eval_set"` (`eval_set` está en `orders`, no en `order_products`) |
| N4 | JSON ✔ | 15.8 s | SQL con subconsulta correlacionada que nunca coincide (`order_number = MIN(order_number) WHERE order_number > o.order_number`), 0 filas |
| N9 | sin JSON ✘ | 57.8 s | el primer SQL falló (promedia columnas que `orders` no tiene) y el reintento pasó el timeout de 30 s |
| N11 | JSON ✔ | 5.8 s | SQL equivalente al de arriba, 1 fila |

Con el modelo de 1.5b, las 5 preguntas analíticas quedan por encima de lo
que puede resolver. Eso ya lo documentaban el README y la Fase 5.

### 3.2 Modelos más grandes: no incluidos

Se intentó correr N1 con `qwen2.5-coder:14b` (esfuerzo Extremo, 300 s). Pasó
el timeout (506 s en total) mientras la suite de tests corría en paralelo, y
la corrida quedó contaminada. Una segunda corrida de N2 y N9 solas con 14b
se canceló a pedido del usuario, para no demorar el commit. **No hay
evidencia de 14b en esta fase.** La UI permite elegir cualquier modelo
descargado desde ⚙️ (Fase 9), y el mismo CLI sirve:
`python -m src.llm.formato_entrega N2 --modelo qwen2.5-coder:14b --timeout 300`.

---

## 4. `docs/ENTREGA_5_NECESIDADES.md` (completo)

Necesidades seleccionadas para la entrega del curso: N1, N2, N4, N9 y N11.

La **necesidad** es la decisión de negocio (`DECISION`) y la **petición** es la pregunta (`PREGUNTA`), copiadas sin cambios de cada módulo de `src/analytics/` (las mismas que `r.decision` y `r.pregunta` en `data/referencia/Nx.json`). Solo la justificación es texto nuevo: cita las tablas y columnas reales del dataset Instacart (`data/instacart.duckdb`, ver `docs/DATASET_INSTACART.md`).

### N1 — Recompra por producto

- **Necesidad:** Qué asegurar en inventario (alta recompra y volumen) y qué revisar o retirar (alto abandono, baja recompra).
- **Petición:** ¿Qué productos se recompran de forma consistente y cuáles se prueban y se abandonan?
- **Por qué se puede responder con esta base de datos:** La columna `order_products.reordered` (0/1) marca por línea si el usuario ya había comprado ese `product_id`, y la secuencia `orders.user_id`/`orders.order_number` permite ver quién lo compra y no lo vuelve a pedir; `products.product_name` da el nombre.

### N2 — Canasta: pares de productos

- **Necesidad:** Paquetes, promociones cruzadas y sugerencias tipo 'te puede interesar'.
- **Petición:** ¿Qué pares de productos se compran juntos con más frecuencia, y cuáles tienen una asociación fuerte aunque sean poco frecuentes?
- **Por qué se puede responder con esta base de datos:** Cada fila de `order_products` (`order_products__prior` + `order_products__train`) liga un `product_id` con su `order_id`, así que los pares que aparecen en el mismo pedido se cuentan directamente.

### N4 — Frecuencia de recompra por cliente

- **Necesidad:** A qué usuarios enviarles un recordatorio de recompra.
- **Petición:** ¿Qué usuarios muestran un intervalo reciente entre pedidos anormalmente largo frente a su propio patrón?
- **Por qué se puede responder con esta base de datos:** `orders.days_since_prior_order` da el intervalo entre pedidos de cada `user_id` y `orders.order_number` los ordena, así que el último intervalo se compara con el patrón propio de cada usuario.

### N9 — Tamaño de pedido

- **Necesidad:** Umbral de incentivo expresado en número de productos (sin montos: el dataset no tiene precios).
- **Petición:** ¿Cómo se distribuye el tamaño del pedido y qué factores se asocian con pedidos más grandes o más chicos?
- **Por qué se puede responder con esta base de datos:** El tamaño del pedido es el número de filas de `order_products` por `order_id`, y los factores candidatos están en `orders` (`order_dow`, `order_hour_of_day`, `days_since_prior_order`, `order_number`).

### N11 — Predicción del siguiente pedido

- **Necesidad:** Referencia de rendimiento alcanzable sin IA para comparar contra el LLM más adelante.
- **Petición:** ¿Qué tan bien predice un baseline determinista (frecuencia+recencia, sin ML) los productos del siguiente pedido?
- **Por qué se puede responder con esta base de datos:** `orders.eval_set` separa el historial de cada usuario ('prior') de su siguiente pedido ('train'), y `order_products__train` contiene los productos que de verdad compró en ese pedido, contra los que se mide la predicción.

### Archivos JSON por necesidad

| Necesidad | Software determinista (punto 3) | IA local (punto 4) |
|---|---|---|
| N1 | `data/referencia/N1.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `N1_ia.json` (botón «Generar JSON de IA para N1» en «📋 Entrega») |
| N2 | `data/referencia/N2.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `N2_ia.json` (botón «Generar JSON de IA para N2» en «📋 Entrega») |
| N4 | `data/referencia/N4.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `N4_ia.json` (botón «Generar JSON de IA para N4» en «📋 Entrega») |
| N9 | `data/referencia/N9.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `N9_ia.json` (botón «Generar JSON de IA para N9» en «📋 Entrega») |
| N11 | `data/referencia/N11.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `N11_ia.json` (botón «Generar JSON de IA para N11» en «📋 Entrega») |

---

## 5. Tests

`tests/test_fase10_entrega.py`, 35 tests, todos contra la fixture chica y
con el modelo simulado (`ModeloSimulado`, el doble ya usado en
`test_app_instacart.py`):

| Parte | Test | Qué comprueba |
|---|---|---|
| — | `test_la_seleccion_es_la_de_la_entrega` | la selección es exactamente N1, N2, N4, N9, N11 |
| A | `test_json_para_descarga_es_el_archivo_en_disco` ×5 | bytes = archivo en disco; JSON válido con `pregunta/decision/valores_clave/hallazgos/consultas` |
| A | `test_json_rancio_se_recalcula_antes_de_servirlo` | JSON con `huella_bd` alterada → se recalcula, se sirve el nuevo, `desde_cache=False` |
| A | `test_boton_descarga_sirve_el_json_en_disco` ×5 | el botón existe en el panel y su URL = sha224 del archivo en disco |
| A | `test_otras_necesidades_no_tienen_descarga_individual` ×6 | N3, N5-N8, N10 sin botón |
| B | `test_json_ia_tiene_la_estructura_exacta` ×5 | claves exactas y en orden; `pregunta == r.pregunta` de `cargar_necesidad`; esa pregunta es la que llegó al prompt de SQL; timestamp ISO con zona |
| B | `test_json_ia_pasa_por_las_guardas_de_seguridad` | `DELETE` → rechazo, sin JSON |
| B | `test_json_ia_pasa_por_la_validacion_de_cifras` | cifra inventada dos veces → plantilla con el valor real |
| B | `test_json_ia_rechaza_necesidades_fuera_de_la_entrega` | N3 → `ValueError` |
| B | `test_seccion_de_entrega_tiene_5_botones_y_el_aviso` | expander, 5 botones en orden, aviso de honestidad |
| B | `test_boton_genera_json_ia_y_ofrece_descarga` ×5 | clic → JSON en sesión → botón `Nx_ia.json` cuya URL = sha224 de ese JSON |
| B | `test_error_de_la_ia_se_muestra_sin_ofrecer_descarga` | rechazo visible, sin botón de descarga |
| B | `test_el_chat_normal_no_cambia` | el expander no aparece en el chat y el chat responde igual |
| C | `test_documento_de_entrega_reusa_pregunta_y_decision_de_analytics` | el .md contiene `PREGUNTA`/`DECISION` exactos y no incluye a las otras 6 |

```text
$ python -m pytest -q
377 passed, 2 xfailed, 1 warning in 320.14s (0:05:20)
# 344 previos (342 passed + 2 xfailed) intactos + 35 nuevos de test_fase10_entrega.py

$ python -m pytest -q tests/test_fase10_entrega.py
35 passed, 1 warning in 10.92s
```

---

## 6. Desviaciones (con motivo)

1. **Sin JSON cuando el pipeline falla.** El enunciado define una estructura
   fija sin campo de error. Empaquetar un fallo exigiría agregar ese campo o
   poner valores vacíos que parecen una respuesta. Se eligió no generar el
   archivo y mostrar el error, igual que el chat.
2. **La Parte B vive en `app_instacart.py`, no en `dashboard.py`.** El
   enunciado la sugiere «en la vista de dashboard», y ahí está (la vista 📊
   de la app). Pero `dashboard.py` se declara «cero IA» desde la Fase 3 y
   también corre standalone sin Ollama, así que no se le agregó una
   dependencia del LLM.
3. **`respuesta_llm` puede venir de la plantilla.** Si la validación de
   cifras rechaza las dos redacciones, `res.respuesta` es la plantilla sin
   modelo. Se usa igual porque es «la redacción que ya genera el chat». La
   UI lo avisa con 🔢, pero el JSON no lo registra (estructura fija).
4. **La evidencia en `docs/evidencia/fase10/` no se versiona**
   (`.gitignore` ignora `docs/evidencia/`, como en las fases anteriores).
   Por eso el contenido de los JSON está copiado arriba.

## 7. Estado final

```text
$ git status --short   # antes del commit
 M src/dashboard/servicio.py
 M src/ui/app_instacart.py
 M src/ui/dashboard.py
?? docs/ENTREGA_5_NECESIDADES.md
?? docs/FASE10_REPORTE.md
?? src/llm/formato_entrega.py
?? tests/test_fase10_entrega.py

# docs/evidencia/fase10/ (capturas y JSON de la IA) queda fuera por .gitignore
```
