# Necesidades de negocio — definiciones formales

Documento de referencia para `src/analytics/`. Cada necesidad declara su
propio universo, y ningún número de este documento se copia a mano: los
valores citados abajo son los medidos al construir los agregados y al
calcular cada necesidad contra `data/instacart.duckdb` (ver
`docs/FASE2A_REPORTE.md` para N1-N6 y `docs/FASE2B_REPORTE.md` para las
correcciones C1-C5 y N7-N11, evidencia completa comando por comando).

**Campos volátiles (C4, huella de contenido):** cada `ResultadoNecesidad`
incluye `huella_contenido` (sha256 de la serialización canónica del resto
del JSON). Los campos EXCLUIDOS de ese hash porque cambian entre corridas
por diseño (no porque el resultado sea distinto) son `fecha_generacion`
(timestamp de la corrida) y `duracion_s` (depende de la carga de la
máquina). Cualquier otro campo debe ser idéntico byte a byte entre dos
corridas con los mismos datos y parámetros; `tests/test_analytics_
determinismo.py` lo verifica sobre las 11 necesidades.

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
- `intervalo_típico` = mediana de `days_since_prior_order` del usuario, **excluyendo el intervalo de su último pedido** (solo intervalos previos). Corrección C1 (Fase 2B): antes incluía ese mismo intervalo en su propia mediana de referencia, lo que sesgaba "típico" hacia "último" y diluía la señal de anomalía — ver `docs/FASE2B_REPORTE.md`.
- `intervalo_último` = valor de `days_since_prior_order` en el pedido con `order_number` máximo del usuario.
- `razón` = último / max(típico, 1).
- `en_riesgo` = razón ≥ `UMBRAL_RAZON` **y** (último − típico) ≥ `UMBRAL_DIAS`.

**Parámetros:**
| Parámetro | Valor | Justificación medida |
|---|---|---|
| `MIN_INTERVALOS` | 5 | Intervalos PREVIOS al último por usuario (universo completo de orders): 100.0% tienen ≥3 previos, 71.0% tienen ≥5, 45.5% tienen ≥10 (antes de C1, contando el intervalo del último pedido: 100.0%/78.9%/49.3% — ver antes/después en `docs/FASE2B_REPORTE.md`). |
| `UMBRAL_RAZON` | 2.0 | Razón entre usuarios elegibles (post-C1): mediana=1.08, P75=1.83, P90=3.00. 2.0 cae entre P75 y P90: selectivo sin ser extremo. |
| `UMBRAL_DIAS` | 5 | Complementa a UMBRAL_RAZON para no marcar como riesgo diferencias triviales cuando el típico es muy corto. |

**Censura:** 11.49% de los 3,214,874 intervalos no nulos del dataset valen
exactamente 30 (tope). De los usuarios "en riesgo" (post-C1), 55.7% tienen
su último intervalo censurado en 30 (su intervalo real podría ser mayor).

**Sensibilidad:** se probaron 4 combinaciones de umbral (razón≥1.5/días≥3,
2.0/5, 2.5/7, 3.0/10); 14.0% de los usuarios evaluables (post-C1) se marcan
"en riesgo" en al menos 3 de las 4.

**Invariante (C5, reforzado):** usuarios evaluables + excluidos por
MIN_INTERVALOS = 206,209, verificado con dos consultas SQL independientes
entre sí (`tests/test_analytics_integration.py::
test_n4_evaluables_mas_excluidos_reconcilia`).

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
- `usuarios_distintos` / `usuarios_primero` (Corrección C2, Fase 2B): cuántos usuarios distintos compraron el producto alguna vez, y cuántos lo tuvieron en posición 1 al menos una vez.

**Parámetros:**
| Parámetro | Valor | Justificación medida |
|---|---|---|
| `MIN_APARICIONES` | 50 | Misma distribución que MIN_LINEAS en N1 (apariciones = líneas por producto): 54.7% de los productos, 98.7% de las líneas. |
| `MIN_USUARIOS_ANCLA` (C2) | 10 | `usuarios_primero` entre los 42,188 productos que fueron primero alguna vez: min=1, P25=2, mediana=6, P75=22. 10 cae entre la mediana y P75: exige que la señal de "ancla" venga de al menos 10 usuarios distintos, no de uno solo comprándolo repetidamente. |

**Elegibilidad (post-C2):** `apariciones >= MIN_APARICIONES` **y**
`usuarios_primero >= MIN_USUARIOS_ANCLA` (antes de C2 solo exigía
MIN_APARICIONES; el antes/después del top 10 y del top 50 está en
`docs/FASE2B_REPORTE.md`).

**Tablas:** top 50 por `veces_primero` (volumen, sobre todos los productos);
top 50 por `índice_ancla` (entre elegibles).

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

## N7 — Evolución por número de pedido

**Pregunta:** ¿cómo cambian tamaño, variedad y recompra a lo largo de la
"vida" de un usuario, pedido a pedido?
**Decisión:** qué ofrecer a clientes nuevos (solo descriptivo, nada causal).

**Universo:** pedidos con líneas, agrupados por `order_number=k`.

**Métricas por k** (solo si `usuarios_n >= MIN_USUARIOS_K`): tamaño medio y
mediana, `tasa_recompra`, `productos_nuevos_por_pedido` (líneas
`reordered=0`), `departamentos_distintos_por_pedido`.

**Composición vs. pareado:** la curva por k mezcla usuarios distintos (solo
llegan a k alto quienes tienen muchos pedidos) — es descriptiva, no una
trayectoria de un mismo cliente. El **análisis pareado** (mismo usuario,
pedido 1 y pedido 10, diferencia media con n y desvío) es la comparación
válida de "primer vs décimo pedido" que pide el brief.

**Parámetro:**
| Parámetro | Valor | Justificación medida |
|---|---|---|
| `MIN_USUARIOS_K` | 100 | Usuarios con al menos k pedidos-con-líneas cae de 206,209 (k=1) a 867 (k=100, el máximo del dataset). Piso convencional de muestra de tres cifras; en este dataset ni siquiera k=100 lo viola, así que no descarta ningún k en la práctica, pero deja el criterio explícito. |

**Medido, no asumido:** 0 de 2,078,068 líneas en order_number=1 tienen
`reordered=1` (0.0000%) — se reporta en limitaciones, no se asume.

**Invariantes:** suma de pedidos por k (TODOS los k) = 3,346,083; suma de
líneas = 33,819,106.

**Rendimiento:** el pipeline en vivo (curva por k + pareado) tarda varios
segundos bajo `threads=1`/4GB por presión de memoria; se precalcula en
`agg_n7_por_k` / `agg_n7_pareado` / `agg_n7_reordered_k1` (construidos en
`agregados.py`, que corre multi-hilo sin ese problema — ver
`docs/FASE2B_REPORTE.md`).

---

## N8 — Rutinarios vs exploradores

**Pregunta:** ¿qué usuarios repiten casi siempre los mismos productos y
cuáles prueban variedad, controlando por exposición?
**Decisión:** a quién ofrecer suscripción/"repetir pedido" y a quién
mostrar novedades.

**Universo:** usuarios con `>= W` pedidos con líneas. **Ventana fija:**
solo los primeros W pedidos con líneas de cada usuario, para que quien
tiene más historial no "gane" variedad solo por tener más oportunidades.

**Métrica:** `índice_novedad` = productos_distintos / líneas en la
ventana (1 = nunca repite; cerca de 0 = siempre lo mismo). Clasificación
por percentiles (P25 rutinario, P75 explorador) — tamaños de grupo fijos
por construcción, por eso se reporta también el histograma completo y los
cortes absolutos.

**Parámetros:**
| Parámetro | Valor | Justificación medida |
|---|---|---|
| `W` | 5 | Usuarios con >=W pedidos con líneas: W=3→100.0%, W=5→84.9% (175,072 de 206,209), W=10→52.1%, W=20→25.6%. W=5 cubre a la gran mayoría sin exigir un historial tan largo como W=10. |
| `percentil_rutinario` / `percentil_explorador` | 0.25 / 0.75 | Cuartiles estándar de índice_novedad. |

**Consistencia medida:** en qué fracción de usuarios `productos_distintos`
coincide exactamente con el número de líneas `reordered=0` en la ventana
(se verifica, no se asume).

**Confusor:** correlación de Pearson entre índice_novedad y tamaño medio de
pedido (pedidos más grandes tienen más oportunidades de producto nuevo).

**Invariante:** usuarios clasificados + excluidos por W = 206,209.

---

## N9 — Tamaño de pedido

**Pregunta:** ¿cómo se distribuye el tamaño del pedido y qué factores se
asocian con pedidos más grandes o más chicos?
**Decisión:** umbral de incentivo/envío gratis expresado en NÚMERO DE
PRODUCTOS (sin precios en el dataset, nunca en montos). Descriptivo, no causal.

**Universo:** pedidos con líneas.

**Métricas:** histograma exacto 1..TAM_MAX + cubo `>=TAM_MAX+1`; media,
mediana, moda, P75, P90; % de pedidos con tamaño `<= k` para una lista de k
candidatos. Por factor (hora, día, intervalo desde el pedido previo,
order_number en cubos): tamaño medio bruto y **desviación intra-usuario**
(tamaño − media de ESE usuario, promediada por cubo) — el bruto mezcla
composición de clientes, el intra-usuario la controla.

**Parámetros:**
| Parámetro | Valor | Justificación medida |
|---|---|---|
| `TAM_MAX` | P99 del tamaño de pedido (35) | Percentil 99, medido en vivo; el histograma exacto llega hasta ahí. |
| `k_candidatos` | (5, 10, 15, 20) | Umbrales candidatos en número de productos para "% de pedidos por debajo de". |
| `MIN_PEDIDOS` | 5 | Mínimo de pedidos con líneas por usuario para entrar al cálculo de desviación intra-usuario (su propio promedio sería poco confiable con muy pocos pedidos); cubre 175,072 usuarios. |

**Invariantes:** suma del histograma = 3,346,083 pedidos; suma de
tamaño×pedidos = 33,819,106.

---

## N10 — Final del carrito / impulso

**Pregunta:** ¿qué productos se agregan sistemáticamente al final del
carrito, más de lo que el tamaño del pedido explicaría?
**Decisión:** qué poner cerca de "caja" (pantalla final) o como sugerencia
de último momento.

**Universo:** líneas de pedidos con tamaño `>= MIN_TAM` (pedidos de 1
producto no tienen posición relativa: dividir por tamaño-1=0). Líneas de
pedidos menores se reportan como excluidas con su conteo.

**Métricas:** `pos_norm = (add_to_cart_order-1)/(tamaño-1)`, 5 tramos de
igual ancho (último cerrado [0.8,1.0]); tasa de recompra por tramo, global
y por departamento. `share_final` = apariciones en el último tramo /
apariciones; `esperado_final` calculado exacto por aparición (mismo enfoque
que N5); `índice_final` = share_final / esperado_final.

**Parámetros:**
| Parámetro | Valor | Justificación medida |
|---|---|---|
| `MIN_TAM` | 2 | Mínimo por definición (pos_norm indefinido en pedidos de 1 producto). Cobertura medida: 3,182,490 de 3,346,083 pedidos (95.1%), 33,655,513 de 33,819,106 líneas (99.5%). |
| `MIN_APARICIONES` | 50 | Mismo criterio que N1/N5: muestra mínima para estimar share_final/índice_final. |
| `MIN_USUARIOS` | 10 | Evita que la señal de "producto de cierre" venga de muy pocos usuarios recurrentes. |
| `percentil_tasa_baja` | 0.25 | Cuartil inferior de tasa_recompra entre elegibles, para aislar candidatos de impulso puro. |

**Tablas:** top 50 por índice_final (todos los productos); top 50 por
índice_final entre elegibles con tasa_recompra baja.

**Invariante:** líneas incluidas + excluidas = 33,819,106.

**Limitación explícita:** el orden de agregado al carrito puede reflejar la
interfaz (listas de "comprar de nuevo" primero), así que "impulso" es
hipótesis, no hecho.

---

## N11 — Predicción del siguiente pedido (baseline determinista, sin ML)

**Pregunta:** ¿qué tan bien predice un baseline determinista
(frecuencia+recencia, sin ML) los productos del siguiente pedido?
**Decisión:** referencia de piso para comparar contra el LLM más adelante.

**Universo:** los 131,209 usuarios cuyo pedido `eval_set='train'` es la
observación objetivo; se predice usando solo sus pedidos `prior`. Los
75,000 usuarios `test` no tienen respuesta conocida y no se evalúan.

**Score:** `score(u,p,DECAY) = Σ DECAY^d` sobre pedidos prior de u que
contienen p, dividido por `Σ DECAY^d` sobre TODOS los pedidos prior de u,
con `d = order_number del último prior − order_number del pedido`.
Desempate determinista: score desc, pedido más reciente, product_id asc.

**Validación sin fuga:** `user_id % 5 = 0` = validación (20%, solo para
elegir DECAY); el 80% restante es evaluación, donde se reporta el
resultado final (también se reporta en validación, para exhibir el
optimismo de medir donde se eligió el parámetro).

**Parámetros:**
| Parámetro | Valor | Justificación medida |
|---|---|---|
| `DECAY` candidatos | 1.0, 0.9, 0.8, 0.7 | Rango convencional de decaimiento exponencial suave a fuerte (1.0 = frecuencia pura). |
| `DECAY` elegido | por hit_rate@10 en validación | Elegido sin mirar el conjunto de evaluación (ver tabla comparativa en `docs/FASE2B_REPORTE.md`). |
| `K` evaluados | 5, 10, 20 | Los tres valores pedidos por el brief. |

**Métricas:** hit-rate, recall@K y precision@K macro, para K∈{5,10,20},
más TECHO alcanzable (% de líneas objetivo cuyo producto ya estaba en el
historial prior — medido con el historial, no con el flag `reordered`).
Baselines de contexto con las mismas métricas: top-K populares globales y
repetir el último pedido prior.

**Interfaz reutilizable:** `predecir_usuario(user_id, k)` devuelve
producto, score y componentes (frecuencia, recencia), leyendo el agregado.

**Invariantes:** usuarios evaluados + excluidos (validación) = 131,209;
suma de líneas objetivo = 1,384,617.

**Rendimiento:** construir el score/ranking para 131,209 usuarios sobre
~13M líneas prior no cabe en 5s en vivo bajo `threads=1`; se precalcula en
`agg_n11_score`/`agg_n11_rank` (armados multi-hilo en `agregados.py`).
