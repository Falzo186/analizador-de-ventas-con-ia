# Entrega: 5 necesidades en formato petición-necesidad

Necesidades seleccionadas para la entrega del curso: N1, N2, N4, N9 y N11.

La **necesidad** es la decisión de negocio (`DECISION`) y la **petición** es la pregunta (`PREGUNTA`), copiadas sin cambios de cada módulo de `src/analytics/` (las mismas que `r.decision` y `r.pregunta` en `data/referencia/Nx.json`). Solo la justificación es texto nuevo: cita las tablas y columnas reales del dataset Instacart (`data/instacart.duckdb`, ver `docs/DATASET_INSTACART.md`).

## N1 — Recompra por producto

- **Necesidad:** Qué asegurar en inventario (alta recompra y volumen) y qué revisar o retirar (alto abandono, baja recompra).
- **Petición:** ¿Qué productos se recompran de forma consistente y cuáles se prueban y se abandonan?
- **Por qué se puede responder con esta base de datos:** La columna `order_products.reordered` (0/1) marca por línea si el usuario ya había comprado ese `product_id`, y la secuencia `orders.user_id`/`orders.order_number` permite ver quién lo compra y no lo vuelve a pedir; `products.product_name` da el nombre.

## N2 — Canasta: pares de productos

- **Necesidad:** Paquetes, promociones cruzadas y sugerencias tipo 'te puede interesar'.
- **Petición:** ¿Qué pares de productos se compran juntos con más frecuencia, y cuáles tienen una asociación fuerte aunque sean poco frecuentes?
- **Por qué se puede responder con esta base de datos:** Cada fila de `order_products` (`order_products__prior` + `order_products__train`) liga un `product_id` con su `order_id`, así que los pares que aparecen en el mismo pedido se cuentan directamente.

## N4 — Frecuencia de recompra por cliente

- **Necesidad:** A qué usuarios enviarles un recordatorio de recompra.
- **Petición:** ¿Qué usuarios muestran un intervalo reciente entre pedidos anormalmente largo frente a su propio patrón?
- **Por qué se puede responder con esta base de datos:** `orders.days_since_prior_order` da el intervalo entre pedidos de cada `user_id` y `orders.order_number` los ordena, así que el último intervalo se compara con el patrón propio de cada usuario.

## N9 — Tamaño de pedido

- **Necesidad:** Umbral de incentivo expresado en número de productos (sin montos: el dataset no tiene precios).
- **Petición:** ¿Cómo se distribuye el tamaño del pedido y qué factores se asocian con pedidos más grandes o más chicos?
- **Por qué se puede responder con esta base de datos:** El tamaño del pedido es el número de filas de `order_products` por `order_id`, y los factores candidatos están en `orders` (`order_dow`, `order_hour_of_day`, `days_since_prior_order`, `order_number`).

## N11 — Predicción del siguiente pedido

- **Necesidad:** Referencia de rendimiento alcanzable sin IA para comparar contra el LLM más adelante.
- **Petición:** ¿Qué tan bien predice un baseline determinista (frecuencia+recencia, sin ML) los productos del siguiente pedido?
- **Por qué se puede responder con esta base de datos:** `orders.eval_set` separa el historial de cada usuario ('prior') de su siguiente pedido ('train'), y `order_products__train` contiene los productos que de verdad compró en ese pedido, contra los que se mide la predicción.

## Archivos JSON por necesidad

| Necesidad | Software determinista (punto 3) | IA local (punto 4) |
|---|---|---|
| N1 | `data/referencia/N1.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `N1_ia.json` (botón «Generar JSON de IA para N1» en «📋 Entrega») |
| N2 | `data/referencia/N2.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `N2_ia.json` (botón «Generar JSON de IA para N2» en «📋 Entrega») |
| N4 | `data/referencia/N4.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `N4_ia.json` (botón «Generar JSON de IA para N4» en «📋 Entrega») |
| N9 | `data/referencia/N9.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `N9_ia.json` (botón «Generar JSON de IA para N9» en «📋 Entrega») |
| N11 | `data/referencia/N11.json` (botón «⬇️ Descargar JSON de esta necesidad» en el panel) | `N11_ia.json` (botón «Generar JSON de IA para N11» en «📋 Entrega») |
