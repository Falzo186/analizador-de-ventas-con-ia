# Necesidades de negocio — definiciones formales

Documento de referencia para `src/analytics/`. Cada necesidad declara su
propio universo, y ningún número de este documento se copia a mano: los
valores citados abajo son los medidos al construir los agregados y al
calcular cada necesidad contra `data/instacart.duckdb` (ver
`docs/FASE2A_REPORTE.md` para la evidencia completa comando por comando).

**Universos comunes** (declarados también dentro de cada `ResultadoNecesidad`):
- **Líneas** = filas de la vista `order_products` (`order_products__prior`
  UNION ALL `order_products__train`): 33,819,106.
- **Pedidos con líneas** = `orders` con `eval_set IN ('prior','train')`: 3,346,083.
- **Pedidos totales** = todos los `orders`: 3,421,083 (incluye 75,000 `test`
  sin líneas).

---

## N1 — Recompra por producto

**Pregunta:** ¿qué productos se recompran de forma consistente y cuáles se
prueban y se abandonan?
**Decisión:** qué asegurar en inventario (alta recompra) y qué revisar o
retirar (alto abandono, baja recompra).

**Universo:** líneas ⨝ `products`.

**Métricas:**
- `tasa_recompra` = (líneas con `reordered=1`) / (líneas totales), por producto.
- Abandono con control de censura: para cada par (usuario, producto), sea
  `f` el `order_number` (sobre pedidos con líneas) del pedido donde el
  usuario compró el producto por primera vez. El par es **observable** si el
  usuario tuvo al menos `K_POST` pedidos con líneas con `order_number > f`.
  Entre pares observables, **abandonó** = cero compras posteriores a `f`.
  `tasa_abandono` = abandonos / observables, por producto.

**Parámetros:**
| Parámetro | Valor | Justificación medida |
|---|---|---|
| `K_POST` | 5 | Con K_POST=3, 80.2% de 13,863,746 pares son observables; con 5, 67.2%; con 10, 46.1%. Se eligió 5: alta potencia estadística sin exigir una ventana irreal frente a la mediana de 10 pedidos/usuario. |
| `MIN_LINEAS` | 50 | Líneas por producto: P25=18, mediana=63, P75=272, máx=491,291. MIN_LINEAS=50 conserva 54.7% de los productos y 98.7% de las líneas. |
| `MIN_OBSERVABLES` | 30 | Observables por producto (con K_POST=5): P25=7, mediana=24, máx=52,255. MIN_OBSERVABLES=30 conserva 45.3% de los productos con muestra suficiente. |
| `percentil_alto` / `percentil_bajo` | 0.75 / 0.25 | Cuartil superior/inferior estándar, aplicado solo sobre productos elegibles. |

**Clasificación** (sobre productos elegibles): "Asegurar inventario" =
volumen y tasa_recompra en P75 o más; "Revisar/retirar" = tasa_abandono en
P75 o más y tasa_recompra en P25 o menos. Productos con aisle/department
'missing' u 'other' se marcan con una columna, sin excluirse.

**Limitaciones:** sin precios (no dice nada de ingreso/ganancia); el 32.8%
de los pares (usuario,producto) no son observables con K_POST=5 y se
excluyen del cálculo de abandono (no cuentan ni como abandono ni como éxito);
el agregado base (`agg_n1_producto`) se precalcula porque el pipeline en
vivo mide ~6s, sobre el objetivo de 5s.

---

## N2 — Canasta: pares de productos

**Pregunta:** ¿qué pares de productos se compran juntos con más frecuencia,
y cuáles tienen asociación fuerte aunque sean poco frecuentes?
**Decisión:** paquetes, promociones cruzadas, "te puede interesar".

**Universo:** pedidos con líneas; pares no ordenados de productos
**candidatos** (no de pedidos) en el mismo pedido.

**Métricas:**
- `soporte` = pedidos_con_par / pedidos con líneas.
- `confianza_a_b` = pedidos_con_par / pedidos_con_a (y simétrica `confianza_b_a`).
- `lift` = pedidos_con_par × pedidos_con_líneas / (pedidos_con_a × pedidos_con_b).

**Parámetros:**
| Parámetro | Valor | Justificación medida (tabla de compromiso) |
|---|---|---|
| `MIN_SOPORTE_PRODUCTO` | 0.002 | 0.0005→3403 productos, 75.2% cobertura, RSS≈3.9GB (excede 3GB); 0.001→1776 productos, 63.9%, RSS≈2.98GB (al límite); **0.002→842 productos, 51.1%, RSS≈1.7GB** (elegido); 0.003→510 productos, 43.1%, RSS≈1.35GB. |
| `MIN_PARES_LIFT` | 100 | Entre los 340,461 pares candidatos: mediana de pedidos_con_par=74, P75=172, P90=418. 100 retiene 135,967 pares (39.9%) por encima de la mediana, evitando que 1-2 co-ocurrencias generen un lift extremo. |

**Tablas:** top 50 por `pedidos_con_par`; top 50 por `lift` restringido a
`pedidos_con_par >= MIN_PARES_LIFT`.

**Limitaciones:** el lift favorece pares raros (productos individualmente
poco frecuentes pueden tener lift alto con pocas co-ocurrencias); los
productos muy populares dominan la tabla de conteo bruto sin que su
asociación sea necesariamente fuerte; el universo de productos está
restringido a los candidatos por diseño (armado no cabe en presupuesto de
tiempo/RAM con el catálogo completo); el armado vive en `src/analytics/
agregados.py`, este módulo solo lee `agg_pares`.

---

## N3 — Picos de demanda

**Pregunta:** ¿en qué combinaciones de día y hora hay más y menos pedidos,
en términos relativos?
**Decisión:** ventanas candidatas para reforzar personal/repartidores
(picos) o promociones (valles) — como índice relativo, nunca dotación
absoluta.

**Universo:** pedidos TOTALES (se cuentan pedidos, no productos; el pedido
'test' sí cuenta porque tiene día/hora válidos).

**Métricas:** matriz 7×24 de conteo de pedidos; `índice` = celda / promedio
de las 168 celdas; `pico` = celda en percentil_pico o más; `valle` = celda en
percentil_valle o menos.

**Parámetros:**
| Parámetro | Valor | Justificación |
|---|---|---|
| `percentil_pico` | 0.90 | Corte convencional de "atípicamente alto" sobre las 168 celdas día×hora. |
| `percentil_valle` | 0.10 | Corte convencional de "atípicamente bajo", simétrico. |

**Limitaciones:** `order_dow`/`order_hour_of_day` son anónimos (etiquetados
"Día 0..6", sin afirmar a qué día real corresponden); prohibido calcular
dotación absoluta de personal/repartidores porque no hay datos de capacidad
en el dataset — la conclusión es solo un índice relativo y ventanas candidatas.

---

## N4 — Frecuencia de recompra por cliente

**Pregunta:** ¿qué usuarios muestran un intervalo reciente entre pedidos
anormalmente largo frente a su propio patrón?
**Decisión:** a quién enviarle un recordatorio de recompra.

**Universo:** pedidos totales, solo tabla `orders` (los intervalos existen
para cualquier `eval_set`); 206,209 usuarios.

**Métricas:**
- `intervalo_típico` = mediana de `days_since_prior_order` del usuario (valores no nulos).
- `intervalo_último` = valor de `days_since_prior_order` en el pedido con `order_number` máximo del usuario.
- `razón` = último / max(típico, 1).
- `en_riesgo` = razón ≥ `UMBRAL_RAZON` **y** (último − típico) ≥ `UMBRAL_DIAS`.

**Parámetros:**
| Parámetro | Valor | Justificación medida |
|---|---|---|
| `MIN_INTERVALOS` | 5 | Intervalos no nulos por usuario (universo completo de orders): 100.0% tienen ≥3, 78.9% tienen ≥5, 49.3% tienen ≥10. |
| `UMBRAL_RAZON` | 2.0 | Razón entre usuarios elegibles: mediana=1.00, P75=1.62, P90=2.61. 2.0 cae entre P75 y P90: selectivo sin ser extremo. |
| `UMBRAL_DIAS` | 5 | Complementa a UMBRAL_RAZON para no marcar como riesgo diferencias triviales cuando el típico es muy corto. |

**Censura:** 11.49% de los 3,214,874 intervalos no nulos del dataset valen
exactamente 30 (tope). De los usuarios "en riesgo", 56.2% tienen su último
intervalo censurado en 30 (su intervalo real podría ser mayor).

**Sensibilidad:** se probaron 4 combinaciones de umbral (razón≥1.5/días≥3,
2.0/5, 2.5/7, 3.0/10); 11.1% de los usuarios evaluables se marcan "en
riesgo" en al menos 3 de las 4.

**Limitación central:** no existe una fecha actual en este dataset: se
detecta un intervalo reciente anormalmente largo *respecto al patrón propio*
del usuario, nunca "días sin comprar hasta hoy".

---

## N5 — Productos ancla

**Pregunta:** ¿qué productos aparecen como primer artículo del carrito más
de lo que su popularidad explicaría?
**Decisión:** qué destacar en portada o primera pantalla.

**Universo:** líneas (`order_products`).

**Métricas:**
- `tasa_primero` = veces con `add_to_cart_order=1` / apariciones del producto.
- `tasa_esperada` = promedio, sobre las apariciones del producto, de 1/tamaño_del_pedido (control por tamaño de pedido).
- `índice_ancla` = tasa_primero / tasa_esperada.

**Parámetro:**
| Parámetro | Valor | Justificación medida |
|---|---|---|
| `MIN_APARICIONES` | 50 | Misma distribución que MIN_LINEAS en N1 (apariciones = líneas por producto): 54.7% de los productos, 98.7% de las líneas. |

**Tablas:** top 50 por `veces_primero` (volumen, sobre todos los productos);
top 50 por `índice_ancla` (entre elegibles, `apariciones >= MIN_APARICIONES`).

**Limitación:** el orden de agregado al carrito puede reflejar la interfaz
(p. ej. listas de "comprar de nuevo" que precargan productos habituales), no
necesariamente una preferencia real — es una hipótesis a validar, no un hecho.

---

## N6 — Departamentos y pasillos

**Pregunta:** ¿qué departamentos/pasillos son "fieles" (alta recompra) de
alto volumen o de nicho, y cuáles son "esporádicos"?
**Decisión:** dónde invertir espacio (en la app o la tienda) y publicidad.

**Universo:** líneas ⨝ `products` ⨝ `aisles`/`departments`.

**Métricas:** por departamento (21) y por pasillo (134): líneas, % del
total, `tasa_recompra`. Cuadrantes cruzando volumen (líneas) y tasa_recompra
contra la **mediana** de cada eje, calculada solo entre las unidades
incluidas: `fiel-alto-volumen`, `fiel-nicho`, `esporadico-alto-volumen`,
`esporadico-nicho`.

**Parámetro:** la mediana de cada eje (no un umbral fijo) — se reporta su
valor medido para departamentos y para pasillos por separado.

**Exclusión explícita:** 'missing' (aisle_id=100, department_id=21) y
'other' (aisle_id=6, department_id=2) se reportan aparte (líneas y % del
total) y NO entran a la clasificación en cuadrantes ni al cálculo de la
mediana de referencia, porque no son categorías de producto reales y
distorsionarían el corte.

**Limitación:** sin precios — "invertir espacio" se basa solo en volumen de
líneas y tasa de recompra, no en margen ni ingreso.

---

## Fase 2B (pendiente)

N7 (evolución por número de pedido), N8 (rutinarios vs. exploradores), N9
(tamaño de pedido), N10 (final del carrito/impulso) y N11 (predicción del
siguiente pedido, baseline determinista) quedan fuera de esta fase.
