# Fase 14 — Regla de JOIN con ejemplo literal y fix del test

Fecha: 2026-10-03 · Rama: `migracion-instacart` (sin commits nuevos desde `c27e4ff`)

## 0. Qué se cambió (confirmado con `git diff --stat`)

```
scripts/benchmark_modelos_llm.py | 337 +++  (Fases 11-13: clasificar_fallo + oráculo; sin cambios en 14)
src/llm/esquema_instacart.py     |  30 +++  (Fase 13: 18)
tests/test_esquema_instacart.py  |  33 +++  (Fase 13: 24)
```

- `src/llm/esquema_instacart.py`: la regla de aisle/department incluye ahora un
  patrón de JOIN literal de 4 líneas (`order_products op → products p → aisles a →
  departments d`) y la advertencia de que `aisle_id`/`department_id` viven en
  `products`, nunca en `order_products*`. La regla de "reordenado" pide además
  usar la vista `order_products` (prior+train) y no solo `__train`.
- `tests/test_esquema_instacart.py`: las pruebas de reglas comparan contra el
  texto con espacios colapsados (ya no se rompen al reenvolver el párrafo).

(Nota: un intento anterior de esta fase, ejecutado cuando los cambios aún no
estaban en el árbol, dio resultados idénticos a la Fase 13; fue reemplazado por
este reporte.)

## 1. Tests dirigidos

`pytest tests/test_esquema_instacart.py tests/test_referencias_benchmark.py -v`
→ **25 passed, 0 failed** (Fase 13: 24 passed, 1 failed). Incluye
`test_regla_reordered_exige_filtrar_por_1` y `test_regla_aisle_department_exige_join_explicito`.

## 2. Benchmark en vivo (`qwen2.5:1.5b`) — `docs/evidencia/benchmark_fase14_prueba.json`

| Métrica | Fase 13 | Fase 14 |
|---|---|---|
| `tasa_sql_valido` (1.er intento) | 69% | **92%** |
| `tasa_sql_valido_final` | 77% | **100%** |
| con reintento | 5 | 2 |
| `resumen_fallos` | `{tabla_o_columna_inexistente: 3}` | `{}` (ningún fallo de ejecución) |
| Correctas según referencia | 5/10 (50%) | **5/12 (42%)** |
| tablas reales / trampas rechazadas | 15/15 · 2/2 | 15/15 · 2/2 |

El denominador del oráculo pasó de 10 a 12 (sin cambios en `benchmark_modelos_llm.py`
entre fases); probablemente porque ahora 2 preguntas más ejecutan y pueden
compararse, pero no se verificó.

**Lectura honesta:** el ejemplo literal eliminó los errores de binder (el SQL ya
siempre corre), pero **no aumentó las respuestas correctas**. Pasar de "no corre"
a "corre y está mal" no es una mejora de calidad; el oráculo lo deja a la vista.

## 3. Las dos preguntas de JOIN

**"¿Qué departamento tiene más líneas de pedido con productos reordenados?"** →
`respuesta_correcta: false`. Ya hace la cadena de 3 tablas correctamente, pero:
```sql
SELECT d.department_id, d.department, COUNT(DISTINCT op.product_id) AS reordered_lines
FROM order_products__train op
JOIN products p ON op.product_id = p.product_id
JOIN departments d ON p.department_id = d.department_id
WHERE op.reordered = 1
GROUP BY d.department_id, d.department
ORDER BY reordered_lines DESC LIMIT 1
```
Errores: `COUNT(DISTINCT op.product_id)` en vez de `COUNT(*)` (cuenta productos
distintos, no líneas) y usa `order_products__train` en vez de la vista
`order_products`, pese a la regla.

**"¿Cuántas líneas de pedido tiene, en promedio, cada pasillo?"** →
`respuesta_correcta: false`. Une `order_products → products → aisles` bien, pero:
```sql
SELECT AVG(p.aisle_id)
FROM order_products op
JOIN products p ON op.product_id = p.product_id
JOIN aisles a ON p.aisle_id = a.aisle_id
```
Promedia un ID (`AVG(p.aisle_id)`) en vez de contar líneas por pasillo y luego
promediar; sin `GROUP BY` ni subconsulta.

## 4. Otras preguntas marcadas incorrectas (SQL válido, respuesta errónea)

- order_dow con más pedidos: traduce 0→'Lunes', … (viola la regla de códigos
  anónimos) y devuelve el nombre en vez del código.
- Pasillo con más productos distintos: agrupa también por `p.product_name`,
  devuelve `aisle_id` y recorre `order_products__train` en vez de `products`.
- Top 5 reordenados: filtra `reordered = 1` (la regla funcionó) pero usa solo
  `__train`.
- Pasillo/departamento del producto 1: JOIN de 3 tablas correcto, pero devuelve
  los IDs además de los nombres (columnas distintas a la referencia).
- Promedio de líneas por pedido: `AVG(order_products.product_id)`, mismo error de
  la Fase 13 (promedio de un ID).
- Mejoró: "pedidos de tipo 'train'" ahora sí filtra `eval_set` (con reintento).

## 5. Suite completa

`pytest` → **2 failed, 409 passed, 2 xpassed** (Fase 13: 3 failed, 408 passed,
1 xfailed, 1 xpassed). Cambió:

| Test | Estado |
|---|---|
| `test_esquema_instacart::test_regla_reordered_exige_filtrar_por_1` | Falla → **pasa** (fix del test) |
| `test_app_instacart::test_modelos_cargados_lee_api_ps` | Sigue fallando (preexistente, Fase 11b) |
| `test_generar_ficha_dataset::...` | Sigue fallando (`data\` vs `data/` en Windows) |
| `xfailed` 1 → 0, `xpassed` 1 → 2 | Un xfail ahora pasa; no se verificó cuál test |

## 6. Conclusión: límite del modelo de 1.5B en joins de 3 tablas

- **Sintaxis/estructura del JOIN: resuelta** con el ejemplo literal en el prompt
  (0 errores de binder, 100% de SQL ejecutable).
- **Semántica: sigue siendo el límite.** El modelo copia la cadena de JOIN pero
  falla en la agregación (`COUNT(DISTINCT)` vs `COUNT(*)`, `AVG` de un ID, falta
  de subconsulta/`GROUP BY`) e ignora reglas secundarias (usar la vista
  `order_products`, no traducir `order_dow`).
- Una sola corrida de un modelo estocástico: las cifras pueden variar entre
  corridas (en Fase 11d/13 una prueba de coherencia cambió de resultado sin tocar
  código). Para una conclusión firme habría que repetir varias corridas.
- La métrica relevante para el proyecto es `correctas según referencia`; la
  `tasa_sql_valido` por sí sola sobrestima la calidad.
