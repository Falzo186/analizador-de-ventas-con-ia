# Ficha del dataset: Instacart Online Grocery Shopping Dataset 2017

_Generado automáticamente por `scripts/generar_ficha_dataset.py` el 2026-09-21. No editar a mano: cada cifra viene de una consulta contra `data/instacart.duckdb`._

## Fuente y licencia

- **Fuente original:** The Instacart Online Grocery Shopping Dataset 2017, https://www.instacart.com/datasets/grocery-shopping-2017
- **Copia usada:** Kaggle, usuario yasserh
- **Licencia:** Licencia original de Instacart: uso no comercial.

## Limitaciones del dataset

- `orders.eval_set` puede ser `prior`, `train` o `test`. Los pedidos `test` no tienen productos en ningún archivo: se excluyen de todo análisis de productos.
- Los productos de un pedido salen de la unión de `order_products__prior` y `order_products__train` (vista `order_products`).
- `days_since_prior_order` es NULL en el primer pedido de cada usuario y está topado en 30.
- `order_dow` (0-6) y `order_hour_of_day` (0-23) son anónimos: no se sabe a qué día real corresponde el 0.
- No hay precios, fechas reales ni datos demográficos: no se puede afirmar nada sobre ingresos o dinero con este dataset.

## Tablas: filas y columnas (medidas con `COUNT(*)` / `PRAGMA table_info`)

| Tabla | Filas | Columnas |
|---|---:|---|
| `aisles` | 134 | aisle_id, aisle |
| `departments` | 21 | department_id, department |
| `products` | 49,688 | product_id, product_name, aisle_id, department_id |
| `orders` | 3,421,083 | order_id, user_id, eval_set, order_number, order_dow, order_hour_of_day, days_since_prior_order |
| `order_products__prior` | 32,434,489 | order_id, product_id, add_to_cart_order, reordered |
| `order_products__train` | 1,384,617 | order_id, product_id, add_to_cart_order, reordered |

`orders` con `eval_set = 'test'`: **75,000** (excluidos de todo análisis de productos).

## Motor y versión

- **DuckDB:** 1.5.5
- **Archivo:** `data/instacart.duckdb` (161.8 MB)

## Resultado de la validación (`src.db.instacart_validacion`)

**22 PASS, 0 FAIL, 13 INFO** — detalle completo en `docs/FASE1_REPORTE.md`.

| Check | Estado | Detalle |
|---|---|---|
| `conteo_orders` | PASS | wc(csv)=3,421,083  COUNT(*) duckdb=3,421,083  esperado=3,421,083 |
| `conteo_order_products__prior` | PASS | wc(csv)=32,434,489  COUNT(*) duckdb=32,434,489  esperado=32,434,489 |
| `conteo_order_products__train` | PASS | wc(csv)=1,384,617  COUNT(*) duckdb=1,384,617  esperado=1,384,617 |
| `conteo_orders_eval_set_test` | PASS | COUNT(*)=75,000  esperado=75,000 |
| `dimensiones_aisles` | INFO | 134 filas, 2 columnas (aisle_id, aisle) |
| `dimensiones_departments` | INFO | 21 filas, 2 columnas (department_id, department) |
| `dimensiones_products` | INFO | 49,688 filas, 4 columnas (product_id, product_name, aisle_id, department_id) |
| `distribucion_eval_set` | INFO | prior=3,214,874, test=75,000, train=131,209 |
| `pk_unica_orders` | PASS | duplicados=0 |
| `pk_unica_products` | PASS | duplicados=0 |
| `pk_unica_aisles` | PASS | duplicados=0 |
| `pk_unica_departments` | PASS | duplicados=0 |
| `pk_unica_order_products` | PASS | duplicados=0 |
| `products_sin_aisle` | PASS | huerfanos=0 |
| `products_sin_department` | PASS | huerfanos=0 |
| `order_products_sin_order` | PASS | huerfanos=0 |
| `order_products_sin_product` | PASS | huerfanos=0 |
| `reordered_rango` | PASS | filas_fuera_de_rango=0 |
| `order_dow_rango` | PASS | filas_fuera_de_rango=0 |
| `order_hour_rango` | PASS | filas_fuera_de_rango=0 |
| `days_since_prior_rango` | PASS | filas_fuera_de_rango=0 |
| `add_to_cart_order_contiguo` | PASS | pedidos_no_contiguos=0 |
| `dspo_null_ssi_orden1` | PASS | excepciones=0 |
| `dspo_no_null_si_no_orden1` | PASS | excepciones=0 |
| `test_sin_lineas` | PASS | excepciones=0 |
| `prior_train_con_al_menos_una_linea` | PASS | excepciones=0 |
| `aisles_missing_other` | INFO | [(6, 'other'), (100, 'missing')] |
| `departments_missing_other` | INFO | [(2, 'other'), (21, 'missing')] |
| `productos_afectados_missing_other` | INFO | productos_afectados=1806 |
| `nulls_aisles` | INFO | sin NULLs |
| `nulls_departments` | INFO | sin NULLs |
| `nulls_products` | INFO | sin NULLs |
| `nulls_orders` | INFO | {'days_since_prior_order': 206209} |
| `nulls_order_products__prior` | INFO | sin NULLs |
| `nulls_order_products__train` | INFO | sin NULLs |
