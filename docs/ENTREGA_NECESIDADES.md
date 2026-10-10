# Entrega: 10 necesidades en formato petición-necesidad

Fase 24: la entrega cubre las 10 necesidades de una sola consulta de `src/analytics/necesidades.py`. En la Fase 28 se reemplazaron N1, N2, N8, N9 y N10 por preguntas más analíticas. Reemplazan a las 11 anteriores; antes se entregaban 5 de ellas: N1, N2, N4, N9 y N11 de la numeración vieja.

La **necesidad** es la decisión de negocio (`decision`) y la **petición** es la pregunta (`pregunta`), copiadas sin cambios de cada definición (las mismas que `r.decision` y `r.pregunta` en `data/referencia/Nx.json`). Solo la justificación es texto nuevo: cita las tablas y columnas reales del dataset Instacart (`data/instacart.duckdb`, ver `docs/DATASET_INSTACART.md`).

## N1 — Clientes de compra más sostenida

- **Necesidad:** A qué clientes dirigir una oferta de fidelización o retención.
- **Petición:** ¿Quiénes son los clientes con la frecuencia de compra más sostenida (más pedidos totales y menor promedio de días entre pedidos consecutivos)?
- **Por qué se puede responder con esta base de datos:** `orders.user_id` identifica al cliente y `orders.days_since_prior_order` da los días desde su pedido anterior; agrupando por cliente se cuentan sus pedidos (`COUNT(order_id)`) y se promedian sus intervalos.

## N2 — Departamento con mayor tasa de recompra

- **Necesidad:** En qué departamento la clientela ya es fiel (y dónde hay que trabajar la recompra).
- **Petición:** ¿Qué departamento tiene la tasa de recompra promedio más alta?
- **Por qué se puede responder con esta base de datos:** `order_products.reordered` (0/1) marca cada recompra; uniendo con `products.department_id` y `departments.department`, la proporción de líneas con `reordered = 1` es la tasa de cada departamento.

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

## N8 — Hora del día con más pedidos

- **Necesidad:** En qué horas reforzar personal, repartidores y capacidad de la app.
- **Petición:** ¿En qué franja horaria del día se concentra el mayor volumen de pedidos?
- **Por qué se puede responder con esta base de datos:** `orders.order_hour_of_day` (0-23) es la hora de cada pedido; contar pedidos por hora da la hora pico.

## N9 — Productos que se compran juntos

- **Necesidad:** Qué par de productos ofrecer como combo u oferta conjunta.
- **Petición:** ¿Cuáles son los dos productos que se compran juntos con más frecuencia (candidatos a combo/oferta)?
- **Por qué se puede responder con esta base de datos:** Dos filas de `order_products` con el mismo `order_id` son dos productos del mismo pedido; un self-join por `order_id` cuenta cuántas veces aparece cada par, y `products.product_name` da los nombres.

## N10 — Pedidos 100% recomprados vs. 100% nuevos

- **Necesidad:** Cuánto del negocio es reposición automática y cuánto es exploración (dónde recomendar).
- **Petición:** ¿Qué porcentaje de los pedidos son 100% de productos recomprados frente a los que son 100% productos nuevos?
- **Por qué se puede responder con esta base de datos:** Por `order_id`, comparar el número de líneas con el de líneas `reordered = 1` dice si el pedido es 100 % recomprado (todas), 100 % nuevo (ninguna) o mixto.

## Archivos JSON por necesidad

Para cada necesidad Nx (N1 a N10):

| Software determinista (punto 3) | IA local (punto 4) |
|---|---|
| `data/referencia/Nx.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `Nx_ia.json` (botón «Generar JSON de IA para Nx» en «📋 Entrega») |
