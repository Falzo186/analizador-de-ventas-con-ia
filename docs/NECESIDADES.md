# Necesidades de negocio — definiciones formales (Fases 24 y 28)

Documento de referencia para `src/analytics/necesidades.py`. Desde la Fase 24
hay **10 necesidades de una sola consulta cada una**: una pregunta simple y
verificable que se responde con UNA sentencia SELECT. Reemplazan a las 11
necesidades anteriores (N1-N11 de las Fases 2A/2B: recompra/abandono,
canasta, picos, frecuencia, ancla, departamentos, evolución, rutinarios,
tamaño, impulso y predicción). Esas mezclaban varias preguntas en una ("y",
"vs.") y obligaban a combinar varias consultas; su definición sigue en el
historial de git (este archivo, antes del commit de la Fase 24) y en
`docs/FASE2A_REPORTE.md` / `docs/FASE2B_REPORTE.md`. Las preguntas
compuestas quedan para una fase futura.

**Fase 28:** N1, N2, N8, N9 y N10 se reemplazaron por preguntas más analíticas
(clientes de compra sostenida, departamento con mayor tasa de recompra, hora
pico, par de productos que más se compran juntos, y pedidos 100 % recomprados
frente a 100 % nuevos). Siguen siendo una sola consulta cada una. N3-N7 no
cambiaron. Las preguntas anteriores de N1, N2, N8, N9 y N10 están en el
historial de git y en `docs/CLASIFICACION_FALLOS.md` («Fase 24»).

Ningún número de este documento se copia a mano en el código: el resultado
de cada necesidad sale de ejecutar su SQL (`python -m src.analytics --todas`)
y se guarda en `data/referencia/Nx.json` con el contrato de siempre
(`ResultadoNecesidad`):

| Parte de la referencia | Campo del JSON |
|---|---|
| SQL de referencia | `consultas` (una sola, `"respuesta"`) |
| Resultado crudo | `tablas["respuesta"]` |
| Redacción en lenguaje natural (generada por código, sin IA) | `hallazgos` |
| La respuesta en sí | `valores_clave` |

El SQL reusa los JOINs y reglas de `REGLAS_DE_NEGOCIO`
(`src/llm/esquema_instacart.py`): `order_products` es la vista prior + train;
el nombre del producto está en `products`; pasillos y departamentos se unen
SOLO a través de `products`; tasa de recompra = recompras / veces comprado con
`HAVING COUNT(*) >= 50`.

**Unidades** (N5): el dataset no tiene cantidades. Cada fila de
`order_products` es un producto dentro de un pedido: una unidad = una línea de
pedido.

**Universos:** líneas = vista `order_products` (33,819,106); pedidos con
productos = 3,346,083; pedidos totales = 3,421,083 (incluye 75,000 `test` sin
productos). Ver `docs/DATASET_INSTACART.md`.

**Caché (Fase 24):** `src.dashboard.servicio` sirve `data/referencia/Nx.json`
solo si su `huella_bd` coincide con la BD **y** su `pregunta` y su SQL
coinciden con la definición actual. Si no, recalcula.

**Forma de la respuesta** (`tipo`), que usan el dashboard y el chequeo
automático del juez (`src/llm/retroalimentacion.py`):
- `lista`: las filas son la respuesta (N1, N3, N4: la pregunta no fija
  cuántas, la referencia guarda 10 por convención).
- `unico`: la respuesta es la primera fila; la referencia guarda el resto como
  contexto (N2, N7, N9: top 10; N8: las 24 horas).
- `escalar`: una fila (N5, N6: una cifra; N10: dos cifras).

Fase 28: la entidad puede identificarse con un **número** (N1: `user_id`; N8:
la hora) o con **dos nombres** (N9: el par), y la respuesta puede tener **más
de una cifra** (N10). `DefNecesidad` lo declara (`nombre_es_texto`,
`columnas_nombre_extra`, `columnas_valor_extra`) y el chequeo automático lo
respeta.

**Parámetros** (los únicos; el PDF verifica estos valores contra el JSON):

| Parámetro | Valor | Dónde | Justificación |
|---|---|---|---|
| `top_n` | 10 | N1, N2, N3, N4, N7, N9 | En N1/N3/N4 es la convención de la referencia (la pregunta no fija cuántos); en las de tipo único es el contexto del ranking. |
| `min_compras` | 50 | N3, N4 | Lo fija la pregunta («al menos 50 compras»). |
| `min_pedidos` | 10 | N1 | Pedidos mínimos de un cliente (la mediana por cliente es 10); no cambia el top, cuyos clientes tienen 100. |
| `min_pedidos_producto` | 10,000 | N9 | Pedidos mínimos de cada producto del par. Es exacto mientras el par número 10 tenga al menos esas co-ocurrencias (tiene 42,283). Sin él, la consulta se queda sin memoria. |

---

## N1 — Clientes de compra más sostenida

**Pregunta:** ¿Quiénes son los clientes con la frecuencia de compra más sostenida (más pedidos totales y menor promedio de días entre pedidos consecutivos)?
**Decisión:** A qué clientes dirigir una oferta de fidelización o retención.
**SQL:** sobre `orders`, por `user_id`: `COUNT(order_id)` (total_pedidos) y
`AVG(days_since_prior_order)` (promedio_dias_entre_pedidos), `HAVING
COUNT(order_id) >= 10`, orden por total desc y promedio asc, `LIMIT 10`.
**Columnas verificadas en el DESCRIBE:** `orders.user_id`,
`orders.days_since_prior_order`.
**Respuesta (medida):** cliente 46408 (100 pedidos, 0.23 días en promedio),
134572 (0.29), 112044 (0.42), … 172806 (0.71).
**Limitaciones:** el dataset guarda como máximo 100 pedidos por cliente (1,374
clientes llegan a ese tope), así que entre los primeros el orden lo decide el
promedio de días. Un promedio menor a 1 día significa varios pedidos el mismo
día: puede ser una cuenta de negocio más que un cliente fiel.

## N2 — Departamento con mayor tasa de recompra

**Pregunta:** ¿Qué departamento tiene la tasa de recompra promedio más alta?
**Decisión:** En qué departamento la clientela ya es fiel (y dónde hay que trabajar la recompra).
**SQL:** por `departments.department` (order_products → products →
departments): `SUM(reordered = 1) * 1.0 / COUNT(*)`, orden desc.
**Decisión de definición:** la tasa del departamento es la de sus líneas (el
«promedio» de `reordered`), no el promedio de las tasas de sus productos.
**Respuesta (medida):** dairy eggs, 67.0 % (3,773,723 de 5,631,067 líneas);
luego beverages (65.4 %) y produce (65.1 %).

## N3 — Tasa de recompra más alta

**Pregunta:** De los productos con al menos 50 compras, ¿cuáles tienen la tasa de recompra más alta?
**Decisión:** Qué productos tienen clientela fiel (candidatos a suscripción o recordatorio).
**SQL:** por `product_name`: `COUNT(*)`, `SUM(reordered = 1)` y su cociente;
`HAVING COUNT(*) >= 50`; orden por tasa desc (desempate: veces comprado desc,
nombre).
**Respuesta (medida):** Raw Veggie Wrappers (65 de 69, 94.2%), Serenity
Ultimate Extrema Overnight Pads (93.3%), Chocolate Love Bar (92.2%), …

## N4 — Tasa de recompra más baja

**Pregunta:** De los productos con al menos 50 compras, ¿cuáles tienen la tasa de recompra más baja?
**Decisión:** Qué productos se prueban y no se vuelven a comprar (revisar o retirar).
**SQL:** la de N3 con orden ascendente.
**Respuesta (medida):** exactamente 8 productos con al menos 50 compras nunca
se recompraron (tasa 0%): Vicks DayQuil and NyQuil Cold & Flu Relief Combo
(95 compras), Organic Castor Oil Skin Care (72), … Gel Food Colors (50). El
orden entre los empatados en 0 es una convención (veces comprado desc,
nombre), no parte de la respuesta.

## N5 — Tamaño promedio del carrito

**Pregunta:** ¿Cuál es el tamaño promedio del carrito (productos por pedido)?
**Decisión:** Referencia para metas de productos por pedido (sin montos: no hay precios).
**SQL:** `AVG` del número de líneas por `order_id` en `order_products`.
**Respuesta (medida):** 10.11 productos por pedido (33,819,106 líneas /
3,346,083 pedidos con productos).

## N6 — Días entre pedidos

**Pregunta:** ¿Cada cuántos días en promedio un cliente vuelve a comprar?
**Decisión:** Cada cuánto conviene enviar un recordatorio de compra.
**SQL:** `AVG(days_since_prior_order)` sobre `orders` (los NULL del primer
pedido no cuentan).
**Decisiones de definición:** incluye los pedidos `test` (tienen intervalo,
aunque no tengan productos); es promedio por pedido, no por cliente.
**Limitación crítica:** el intervalo está censurado en 30, así que el promedio
real es mayor.
**Respuesta (medida):** 11.11 días (3,214,874 pedidos con intervalo; sin los
`test` sería 10.97).

## N7 — Pasillo con más productos distintos

**Pregunta:** ¿Qué pasillo tiene más productos distintos a la venta?
**Decisión:** Qué pasillos concentran el surtido (dónde simplificar o reforzar el catálogo).
**SQL:** `COUNT(*)` de `products` por `aisles.aisle` (catálogo).
**Decisión de definición:** "a la venta" = en el catálogo. Contar productos
distintos que sí aparecen en `order_products` da el mismo top en este dataset.
**Respuesta (medida):** `missing` (1,258), el pasillo comodín de productos sin
pasillo asignado; el primer pasillo real es candy chocolate (1,246). La
redacción de la referencia dice ambas cosas.

## N8 — Hora del día con más pedidos

**Pregunta:** ¿En qué franja horaria del día se concentra el mayor volumen de pedidos?
**Decisión:** En qué horas reforzar personal, repartidores y capacidad de la app.
**SQL:** `COUNT(*)` de `orders` por `order_hour_of_day`, orden desc (las 24 horas).
**Columna verificada en el DESCRIBE:** `orders.order_hour_of_day` (0-23).
**Decisión de definición:** la franja es la hora entera; se cuentan pedidos
(incluidos los `test`), no productos.
**Respuesta (medida):** la hora 10, con 288,418 pedidos (8.4 % de 3,421,083);
luego las horas 11 (284,728) y 15 (283,639). La hora es anónima (zona horaria
desconocida).

## N9 — Productos que se compran juntos

**Pregunta:** ¿Cuáles son los dos productos que se compran juntos con más frecuencia (candidatos a combo/oferta)?
**Decisión:** Qué par de productos ofrecer como combo u oferta conjunta.
**SQL:** self-join de `order_products` por `order_id` con
`a.product_id < b.product_id`, entre los productos con al menos 10,000
pedidos, `COUNT(*)` por par, y JOIN a `products` (dos veces) para los nombres.
Una sola sentencia: sin `WITH` (la guarda del chat solo acepta `SELECT`), con
subconsultas en el `FROM`.
**Por qué el umbral:** sin él, el self-join sobre 33.8M líneas se queda sin
memoria con el límite de 4 GB del proyecto (medido: 79 s y Out of Memory). Es
exacto: un par con K co-ocurrencias exige K pedidos de cada producto, y el par
número 10 tiene 42,283 ≥ 10,000 (con 5,000 el top sale idéntico). Tarda ~7 s
con `threads=1` (la capa analítica) y 1.4 s en la conexión de la guarda del chat.
**Límite propio (Fase 29):** cuando un modelo genera el SQL de N9 (panel de
iteraciones y entrega de IA), la guarda de DuckDB corta a los 25 s, no a los 10 s
del chat (`DefNecesidad.timeout_consulta_s`).
**Respuesta (medida):** Bag of Organic Bananas + Organic Hass Avocado (64,761
pedidos juntos); luego Bag of Organic Bananas + Organic Strawberries (64,702)
y Organic Strawberries + Banana (58,330).
**Limitaciones:** mide volumen, no afinidad (los productos más vendidos salen
juntos por ser populares). Además, el dataset no tiene precios.

## N10 — Pedidos 100 % recomprados vs. 100 % nuevos

**Pregunta:** ¿Qué porcentaje de los pedidos son 100% de productos recomprados frente a los que son 100% productos nuevos?
**Decisión:** Cuánto del negocio es reposición automática y cuánto es exploración (dónde recomendar).
**SQL:** por `order_id` (subconsulta), cuántas líneas tiene y cuántas son
`reordered = 1`; afuera, el % de pedidos con todas recompradas y con ninguna.
Una sola consulta y una sola fila con las dos cifras.
**Respuesta (medida):** 21.49 % de los pedidos son 100 % recomprados (719,207,
«piloto automático») y 11.87 % son 100 % nuevos (397,115, «exploratorios»), de
3,346,083 pedidos con productos; el 66.64 % mezcla las dos cosas.
**Limitaciones:** el primer pedido de cada cliente es 100 % nuevo por
definición, así que infla los «exploratorios».
