# Necesidades de negocio — definiciones formales (Fase 24)

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

**Unidades:** el dataset no tiene cantidades. Cada fila de `order_products`
es un producto dentro de un pedido: una unidad = una línea de pedido.

**Universos:** líneas = vista `order_products` (33,819,106); pedidos con
productos = 3,346,083; pedidos totales = 3,421,083 (incluye 75,000 `test` sin
productos). Ver `docs/DATASET_INSTACART.md`.

**Caché (Fase 24):** `src.dashboard.servicio` sirve `data/referencia/Nx.json`
solo si su `huella_bd` coincide con la BD **y** su `pregunta` y su SQL
coinciden con la definición actual. Si no, recalcula.

**Forma de la respuesta** (`tipo`), que usan el dashboard y el chequeo
automático del juez (`src/llm/retroalimentacion.py`):
- `lista`: las filas son la respuesta (N1: 10, fijado por la pregunta;
  N3/N4: la pregunta no fija cuántas, la referencia guarda 10 por convención).
- `unico`: la respuesta es la primera fila; la referencia guarda el top 10
  como contexto (N2, N7, N8, N9).
- `escalar`: una fila, un número (N5, N6, N10).

**Parámetros** (los únicos; el PDF verifica estos valores contra el JSON):

| Parámetro | Valor | Dónde | Justificación |
|---|---|---|---|
| `top_n` | 10 | N1, N2, N3, N4, N7, N8, N9 | N1 pide los 10 primeros; en N3/N4 es la convención de la referencia; en las de tipo único es el contexto del ranking. |
| `min_compras` | 50 | N3, N4 | Lo fija la pregunta («al menos 50 compras»). |

---

## N1 — Productos más vendidos

**Pregunta:** ¿Cuáles son los 10 productos más vendidos por número de unidades?
**Decisión:** Qué productos asegurar en inventario por volumen.
**SQL:** `COUNT(*)` de `order_products` unido con `products`, agrupado por
`product_name`, orden descendente, `LIMIT 10`.
**Respuesta (medida):** Banana (491,291 unidades), Bag of Organic Bananas
(394,930), Organic Strawberries (275,577), … Organic Whole Milk (142,813).

## N2 — Departamento con más pedidos

**Pregunta:** ¿Qué departamento tiene más pedidos en total?
**Decisión:** Qué departamento conviene priorizar en surtido y visibilidad.
**SQL:** `COUNT(DISTINCT op.order_id)` por `departments.department`
(order_products → products → departments).
**Decisión de definición:** "pedidos" = pedidos distintos que tienen al menos
un producto del departamento. Contar líneas da el mismo primer lugar en este
dataset (produce), pero con otra cifra: es otra métrica.
**Respuesta (medida):** produce, en 2,506,247 pedidos distintos.

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

## N8 — Pasillo con más unidades vendidas

**Pregunta:** ¿Qué pasillo tiene más unidades vendidas en total?
**Decisión:** Qué pasillo mueve más volumen (reposición, espacio, personal).
**SQL:** `COUNT(*)` de `order_products` por `aisles.aisle` (vía `products`).
**Respuesta (medida):** fresh fruits (3,792,661 unidades), luego fresh
vegetables (3,568,630).

## N9 — Producto que se agrega primero

**Pregunta:** ¿Cuál es el producto que más veces se agrega primero al carrito (add_to_cart_order = 1)?
**Decisión:** Qué producto motiva la compra (candidato a portada o a promoción de entrada).
**SQL:** `COUNT(*)` por `product_name` con `add_to_cart_order = 1`.
**Respuesta (medida):** Banana (115,521 veces), luego Bag of Organic Bananas
(82,877).

## N10 — Porcentaje de líneas recompradas

**Pregunta:** ¿Qué porcentaje de todas las líneas de pedido corresponde a productos recomprados (reordered = 1)?
**Decisión:** Qué tanto del negocio viene de compras repetidas (peso de la fidelidad).
**SQL:** `SUM(reordered = 1) * 100.0 / COUNT(*)` sobre `order_products`.
**Respuesta (medida):** 59.01% (19,955,360 de 33,819,106 líneas).
