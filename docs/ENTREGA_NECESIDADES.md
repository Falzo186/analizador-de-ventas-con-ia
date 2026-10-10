# Entrega: 10 necesidades en formato petición-necesidad

Fase 24: la entrega cubre las 10 necesidades de una sola consulta de `src/analytics/necesidades.py`. Reemplazan a las 11 anteriores; antes se entregaban 5 de ellas: N1, N2, N4, N9 y N11 de la numeración vieja.

La **necesidad** es la decisión de negocio (`decision`) y la **petición** es la pregunta (`pregunta`), copiadas sin cambios de cada definición (las mismas que `r.decision` y `r.pregunta` en `data/referencia/Nx.json`). Solo la justificación es texto nuevo: cita las tablas y columnas reales del dataset Instacart (`data/instacart.duckdb`, ver `docs/DATASET_INSTACART.md`).

## N1 — Productos más vendidos

- **Necesidad:** Qué productos asegurar en inventario por volumen.
- **Petición:** ¿Cuáles son los 10 productos más vendidos por número de unidades?
- **Por qué se puede responder con esta base de datos:** Cada fila de `order_products` (`__prior` + `__train`) es un producto dentro de un pedido; contar filas por `product_id` y unir con `products.product_name` da las unidades por producto (no hay columna de cantidad: una unidad = una línea).

## N2 — Departamento con más pedidos

- **Necesidad:** Qué departamento conviene priorizar en surtido y visibilidad.
- **Petición:** ¿Qué departamento tiene más pedidos en total?
- **Por qué se puede responder con esta base de datos:** `order_products.order_id` se une con `products.department_id` y `departments.department`; `COUNT(DISTINCT order_id)` por departamento cuenta los pedidos que tienen al menos un producto de él.

## N3 — Tasa de recompra más alta

- **Necesidad:** Qué productos tienen clientela fiel (candidatos a suscripción o recordatorio).
- **Petición:** De los productos con al menos 50 compras, ¿cuáles tienen la tasa de recompra más alta?
- **Por qué se puede responder con esta base de datos:** `order_products.reordered` (0/1) marca si el usuario ya había comprado ese producto; la tasa es recompras / veces comprado por producto, con `HAVING COUNT(*) >= 50`.

## N4 — Tasa de recompra más baja

- **Necesidad:** Qué productos se prueban y no se vuelven a comprar (revisar o retirar).
- **Petición:** De los productos con al menos 50 compras, ¿cuáles tienen la tasa de recompra más baja?
- **Por qué se puede responder con esta base de datos:** La misma tasa que N3 (`reordered` / total por producto), ordenada de menor a mayor.

## N5 — Tamaño promedio del carrito

- **Necesidad:** Referencia para metas de productos por pedido (sin montos: no hay precios).
- **Petición:** ¿Cuál es el tamaño promedio del carrito (productos por pedido)?
- **Por qué se puede responder con esta base de datos:** El tamaño de un pedido es el número de filas de `order_products` con su `order_id`; el promedio se toma sobre los pedidos con productos.

## N6 — Días entre pedidos

- **Necesidad:** Cada cuánto conviene enviar un recordatorio de compra.
- **Petición:** ¿Cada cuántos días en promedio un cliente vuelve a comprar?
- **Por qué se puede responder con esta base de datos:** `orders.days_since_prior_order` es el intervalo desde el pedido anterior del mismo `user_id` (NULL en el primero, censurado en 30).

## N7 — Pasillo con más productos distintos

- **Necesidad:** Qué pasillos concentran el surtido (dónde simplificar o reforzar el catálogo).
- **Petición:** ¿Qué pasillo tiene más productos distintos a la venta?
- **Por qué se puede responder con esta base de datos:** El catálogo es la tabla `products`, con su `aisle_id`; unirla con `aisles.aisle` y contar productos por pasillo.

## N8 — Pasillo con más unidades vendidas

- **Necesidad:** Qué pasillo mueve más volumen (reposición, espacio, personal).
- **Petición:** ¿Qué pasillo tiene más unidades vendidas en total?
- **Por qué se puede responder con esta base de datos:** Las líneas de `order_products` se unen con `products.aisle_id` y `aisles.aisle`; contar líneas por pasillo da las unidades.

## N9 — Producto que se agrega primero

- **Necesidad:** Qué producto motiva la compra (candidato a portada o a promoción de entrada).
- **Petición:** ¿Cuál es el producto que más veces se agrega primero al carrito (add_to_cart_order = 1)?
- **Por qué se puede responder con esta base de datos:** `order_products.add_to_cart_order` es la posición de cada producto en el carrito; filtrar la posición 1 y contar por `products.product_name`.

## N10 — Porcentaje de líneas recompradas

- **Necesidad:** Qué tanto del negocio viene de compras repetidas (peso de la fidelidad).
- **Petición:** ¿Qué porcentaje de todas las líneas de pedido corresponde a productos recomprados (reordered = 1)?
- **Por qué se puede responder con esta base de datos:** `order_products.reordered` vale 1 en cada línea de un producto que el usuario ya había comprado; su proporción sobre todas las líneas es el porcentaje.

## Archivos JSON por necesidad

Para cada necesidad Nx (N1 a N10):

| Software determinista (punto 3) | IA local (punto 4) |
|---|---|
| `data/referencia/Nx.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `Nx_ia.json` (botón «Generar JSON de IA para Nx» en «📋 Entrega») |
