"""Agregados precalculados para necesidades que no caben en un panel interactivo.

Dos necesidades lo requieren, medido en `docs/FASE2A_REPORTE.md`:

- **N2** (canasta): armar todos los pares de productos co-ocurrentes, incluso
  restringido a un subconjunto de productos candidatos, toma varios segundos
  y GBs de RAM — la tabla de compromiso completa está en el reporte.
- **N1** (recompra/abandono): el pipeline completo en vivo (agregado base +
  la lógica de abandono con control de censura, que necesita agrupar 33.8M
  líneas en ~13.8M pares usuario-producto) midió **~6.0s en caliente**
  (1.9s agregado base + 2.4s construir pares usuario-producto + 1.4s
  agregación de abandono), por encima del objetivo de <5s del brief. Se
  documenta la medición exacta en el reporte de esta fase.

Ambas se arman UNA VEZ aquí, en un archivo separado `data/instacart_agg.duckdb`
(ignorado por git). Los módulos de necesidad (`n1_recompra.py`,
`n2_canasta.py`) solo LEEN sus tablas ya armadas — el armado no vive ahí a
propósito, así puede reconstruirse (`--rebuild`) sin tocar qué hallazgos o
tablas genera cada necesidad.

`agg_meta` guarda la huella de la BD fuente (conteos de `src.analytics.
conexion.huella_bd`) en el momento de construir. `abrir_agregado()` compara
esa huella contra la huella actual de la BD fuente: si no coinciden, el
agregado es "rancio" y se rechaza con `AgregadoRancioError` en vez de servir
datos desactualizados en silencio.

CLI:
    python -m src.analytics.agregados --db data/instacart.duckdb --out data/instacart_agg.duckdb --rebuild
    python -m src.analytics.agregados --tabla-compromiso --db data/instacart.duckdb --valores 0.0005,0.001,0.002,0.003
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import duckdb

from src.analytics.conexion import MEMORY_LIMIT, huella_bd, version_duckdb

# --- N2: canasta -----------------------------------------------------------
MIN_SOPORTE_PRODUCTO_DEFAULT = 0.002
MIN_PARES_LIFT_DEFAULT = 100
# Justificación medida (tabla de compromiso completa en docs/FASE2A_REPORTE.md):
#   0.0005 -> 3403 productos, 75.20% cobertura, 340k-4.5M pares, RSS ~3.9GB (EXCEDE el límite de 3GB)
#   0.001  -> 1776 productos, 63.86% cobertura, RSS ~2.98GB (al límite)
#   0.002  -> 842 productos,  51.05% cobertura, RSS ~1.7GB  (elegido: margen claro bajo 3GB)
#   0.003  -> 510 productos,  43.13% cobertura, RSS ~1.35GB (más barato, cubre menos)

# --- N1: recompra/abandono ---------------------------------------------------
K_POST_DEFAULT = 5

# --- N10: final del carrito --------------------------------------------------
MIN_TAM_N10_DEFAULT = 2
# Justificación medida (ver docs/FASE2A_REPORTE.md): con K_POST=3, 80.2% de los
# 13.86M pares (usuario,producto) son "observables"; con K_POST=5, 67.2%
# (9.32M); con K_POST=10, 46.1%. La distribución de pedidos-con-líneas por
# usuario tiene mediana 10 y P25=6 -- pedir 5 pedidos posteriores da una
# ventana de "oportunidad de recompra" comparable a la mitad del historial
# típico de un usuario, sin descartar la mayoría de los pares (67% quedan
# observables) como sí ocurre con K_POST=10 (46%).


class AgregadoRancioError(RuntimeError):
    """La huella de la BD fuente ya no coincide con la que tenía el agregado al construirse."""


# ---------------------------------------------------------------------------
# N2: pares de productos
# ---------------------------------------------------------------------------

def _sql_construir_pares(min_pedidos_candidato: int) -> str:
    return f"""
    CREATE TABLE agg_candidatos AS
    WITH pp AS (
        SELECT op.product_id, COUNT(DISTINCT op.order_id) AS pedidos_con_producto
        FROM src.order_products op
        JOIN src.orders o ON o.order_id = op.order_id
        WHERE o.eval_set IN ('prior', 'train')
        GROUP BY op.product_id
    )
    SELECT product_id, pedidos_con_producto
    FROM pp
    WHERE pedidos_con_producto >= {min_pedidos_candidato};

    CREATE TEMP TABLE filtrado AS
    SELECT op.order_id, op.product_id
    FROM src.order_products op
    JOIN src.orders o ON o.order_id = op.order_id
    WHERE o.eval_set IN ('prior', 'train')
      AND op.product_id IN (SELECT product_id FROM agg_candidatos);

    CREATE TABLE agg_pares AS
    WITH crudo AS (
        SELECT a.product_id AS producto_a, b.product_id AS producto_b,
               COUNT(*) AS pedidos_con_par
        FROM filtrado a
        JOIN filtrado b ON a.order_id = b.order_id AND a.product_id < b.product_id
        GROUP BY a.product_id, b.product_id
    ),
    total AS (
        SELECT COUNT(*) AS n FROM src.orders WHERE eval_set IN ('prior', 'train')
    )
    SELECT
        c.producto_a, c.producto_b, c.pedidos_con_par,
        c.pedidos_con_par * 1.0 / t.n AS soporte,
        c.pedidos_con_par * 1.0 / ca.pedidos_con_producto AS confianza_a_b,
        c.pedidos_con_par * 1.0 / cb.pedidos_con_producto AS confianza_b_a,
        (c.pedidos_con_par * 1.0 * t.n) / (ca.pedidos_con_producto * 1.0 * cb.pedidos_con_producto) AS lift
    FROM crudo c
    JOIN agg_candidatos ca ON ca.product_id = c.producto_a
    JOIN agg_candidatos cb ON cb.product_id = c.producto_b
    CROSS JOIN total t;

    DROP TABLE filtrado;
    """


# ---------------------------------------------------------------------------
# N1: recompra por producto + abandono con control de censura
# ---------------------------------------------------------------------------

def _sql_construir_n1(k_post: int) -> str:
    return f"""
    CREATE TABLE agg_n1_base AS
    SELECT product_id,
           COUNT(*) AS lineas,
           SUM(CASE WHEN reordered = 1 THEN 1 ELSE 0 END) AS lineas_recompra
    FROM src.order_products
    GROUP BY product_id;

    CREATE TEMP TABLE n1_usuarios AS
    SELECT op.product_id, COUNT(DISTINCT o.user_id) AS usuarios_distintos
    FROM src.order_products op
    JOIN src.orders o ON o.order_id = op.order_id
    GROUP BY op.product_id;

    CREATE TEMP TABLE n1_lineas_usuario AS
    SELECT user_id, order_number,
           SUM(1) OVER (PARTITION BY user_id ORDER BY order_number
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS acumulado
    FROM src.orders WHERE eval_set IN ('prior', 'train');

    CREATE TEMP TABLE n1_total_usuario AS
    SELECT user_id, COUNT(*) AS n_total
    FROM src.orders WHERE eval_set IN ('prior', 'train') GROUP BY user_id;

    CREATE TEMP TABLE n1_compras AS
    SELECT o.user_id, op.product_id, MIN(o.order_number) AS f, COUNT(*) AS n_compras
    FROM src.order_products op
    JOIN src.orders o ON o.order_id = op.order_id
    WHERE o.eval_set IN ('prior', 'train')
    GROUP BY o.user_id, op.product_id;

    CREATE TEMP TABLE n1_abandono AS
    WITH j AS (
        SELECT cup.product_id,
               (tu.n_total - lu.acumulado) AS n_posteriores,
               cup.n_compras
        FROM n1_compras cup
        JOIN n1_lineas_usuario lu ON lu.user_id = cup.user_id AND lu.order_number = cup.f
        JOIN n1_total_usuario tu ON tu.user_id = cup.user_id
    )
    SELECT product_id,
           SUM(CASE WHEN n_posteriores >= {k_post} THEN 1 ELSE 0 END) AS observables,
           SUM(CASE WHEN n_posteriores >= {k_post} AND n_compras = 1 THEN 1 ELSE 0 END) AS abandonos
    FROM j
    GROUP BY product_id;

    CREATE TABLE agg_n1_producto AS
    SELECT b.product_id, b.lineas, u.usuarios_distintos, b.lineas_recompra,
           b.lineas_recompra * 1.0 / b.lineas AS tasa_recompra,
           COALESCE(a.observables, 0) AS observables,
           COALESCE(a.abandonos, 0) AS abandonos,
           CASE WHEN COALESCE(a.observables, 0) > 0
                THEN a.abandonos * 1.0 / a.observables ELSE NULL END AS tasa_abandono
    FROM agg_n1_base b
    LEFT JOIN n1_usuarios u ON u.product_id = b.product_id
    LEFT JOIN n1_abandono a ON a.product_id = b.product_id;

    DROP TABLE n1_usuarios;
    DROP TABLE n1_lineas_usuario;
    DROP TABLE n1_total_usuario;
    DROP TABLE n1_compras;
    DROP TABLE n1_abandono;
    """


# ---------------------------------------------------------------------------
# N7/N8/N9: features por pedido (con líneas)
# ---------------------------------------------------------------------------
# Medido: construir esta tabla en vivo tarda ~10.7s (3.6s del tamaño/recompra
# + ~6.9s del COUNT(DISTINCT department_id) por pedido), sobre el objetivo de
# 5s. Se precalcula una sola vez y la reusan N7 (agrupa por k), N8 (ventana
# de los primeros W pedidos por usuario) y N9 (histograma y factores).

_SQL_PEDIDO = """
CREATE TABLE agg_pedido AS
SELECT o.order_id, o.user_id, o.order_number AS k,
       o.order_dow, o.order_hour_of_day, o.days_since_prior_order,
       b.tamano, b.lineas_recompra, b.productos_nuevos, d.departamentos_distintos
FROM src.orders o
JOIN (
    SELECT order_id, COUNT(*) AS tamano,
           SUM(CASE WHEN reordered = 1 THEN 1 ELSE 0 END) AS lineas_recompra,
           SUM(CASE WHEN reordered = 0 THEN 1 ELSE 0 END) AS productos_nuevos
    FROM src.order_products GROUP BY order_id
) b ON b.order_id = o.order_id
JOIN (
    SELECT op.order_id, COUNT(DISTINCT p.department_id) AS departamentos_distintos
    FROM src.order_products op JOIN src.products p ON p.product_id = op.product_id
    GROUP BY op.order_id
) d ON d.order_id = o.order_id
WHERE o.eval_set IN ('prior', 'train');
"""

# N7 agrega por k y compara el pedido 1 vs 10 (pareado). Medido: bajo
# threads=1 (obligatorio para determinismo, ver conexion.py) con
# memory_limit=4GB, correr el GROUP BY por k y luego el JOIN pareado EN LA
# MISMA conexión mide ~12s en vivo (el JOIN pareado solo, justo después del
# GROUP BY, se vuelve ~70x más lento que en aislado: 7.2s vs 0.1s — una
# presión de memoria/buffer bajo un solo hilo, no un problema de tamaño de
# datos). Se precalculan ambos aquí, en el build (multi-hilo, sin este
# patrón), y N7 solo lee.
K_PAREADO_A_N7, K_PAREADO_B_N7 = 1, 10

_SQL_N7 = f"""
CREATE TABLE agg_n7_por_k AS
SELECT k, COUNT(*) AS pedidos, COUNT(DISTINCT user_id) AS usuarios_n,
       AVG(tamano) AS tamano_medio, MEDIAN(tamano) AS tamano_mediana,
       SUM(lineas_recompra) * 1.0 / SUM(tamano) AS tasa_recompra,
       AVG(productos_nuevos) AS productos_nuevos_por_pedido,
       AVG(departamentos_distintos) AS departamentos_distintos_por_pedido
FROM agg_pedido
GROUP BY k
ORDER BY k;

CREATE TABLE agg_n7_pareado AS
WITH p1 AS (SELECT user_id, tamano, lineas_recompra, productos_nuevos, departamentos_distintos
            FROM agg_pedido WHERE k = {K_PAREADO_A_N7}),
     p10 AS (SELECT user_id, tamano, lineas_recompra, productos_nuevos, departamentos_distintos
             FROM agg_pedido WHERE k = {K_PAREADO_B_N7}),
     diffs AS (
        SELECT p10.tamano - p1.tamano AS d_tamano,
               (p10.lineas_recompra * 1.0 / p10.tamano) - (p1.lineas_recompra * 1.0 / p1.tamano) AS d_tasa_recompra,
               p10.productos_nuevos - p1.productos_nuevos AS d_productos_nuevos,
               p10.departamentos_distintos - p1.departamentos_distintos AS d_departamentos_distintos
        FROM p1 JOIN p10 ON p10.user_id = p1.user_id
     )
SELECT COUNT(*) AS n_usuarios,
       AVG(d_tamano) AS diff_tamano_media, STDDEV_SAMP(d_tamano) AS diff_tamano_std,
       AVG(d_tasa_recompra) AS diff_tasa_recompra_media, STDDEV_SAMP(d_tasa_recompra) AS diff_tasa_recompra_std,
       AVG(d_productos_nuevos) AS diff_productos_nuevos_media, STDDEV_SAMP(d_productos_nuevos) AS diff_productos_nuevos_std,
       AVG(d_departamentos_distintos) AS diff_departamentos_distintos_media,
       STDDEV_SAMP(d_departamentos_distintos) AS diff_departamentos_distintos_std
FROM diffs;

CREATE TABLE agg_n7_reordered_k1 AS
SELECT SUM(tamano) AS lineas_k1, SUM(lineas_recompra) AS lineas_k1_reordered
FROM agg_pedido WHERE k = 1;
"""


# ---------------------------------------------------------------------------
# N10: producto de cierre de carrito
# ---------------------------------------------------------------------------
# Medido: un COUNT(DISTINCT user_id) por producto sobre las líneas de N10
# (pedidos con tamaño >= MIN_TAM) tarda varios segundos por sí solo (mismo
# costo que N1/N5). Se precalcula junto con esperado_final (promedio, por
# aparición, de la fracción de posiciones del pedido que caen en el último
# tramo — mismo enfoque que tasa_esperada de N5).

def _sql_construir_n10(min_tam: int) -> str:
    return f"""
    CREATE TEMP TABLE n10_tam AS
    SELECT order_id, COUNT(*) AS tamano FROM src.order_products
    GROUP BY order_id HAVING COUNT(*) >= {min_tam};

    CREATE TEMP TABLE n10_lineas AS
    SELECT op.order_id, op.product_id, op.add_to_cart_order, op.reordered, t.tamano,
           (op.add_to_cart_order - 1.0) / (t.tamano - 1) AS pos_norm,
           (t.tamano - CAST(CEIL(0.8 * (t.tamano - 1) + 1) AS INTEGER) + 1) * 1.0 / t.tamano AS frac_ultimo_tramo
    FROM src.order_products op
    JOIN n10_tam t ON t.order_id = op.order_id;

    CREATE TABLE agg_n10_producto AS
    SELECT product_id,
           COUNT(*) AS apariciones,
           SUM(CASE WHEN pos_norm >= 0.8 THEN 1 ELSE 0 END) AS apariciones_ultimo_tramo,
           SUM(CASE WHEN reordered = 1 THEN 1 ELSE 0 END) AS lineas_recompra,
           AVG(frac_ultimo_tramo) AS esperado_final,
           COUNT(DISTINCT ol.user_id) AS usuarios_distintos
    FROM n10_lineas l
    JOIN src.orders ol ON ol.order_id = l.order_id
    GROUP BY product_id;

    DROP TABLE n10_lineas;
    DROP TABLE n10_tam;
    """


# ---------------------------------------------------------------------------
# N11: baseline de predicción del siguiente pedido
# ---------------------------------------------------------------------------
# Precalcula, para cada usuario cuyo ÚLTIMO pedido es 'train', el score de
# cada producto que compró alguna vez en sus pedidos 'prior', para cada
# DECAY candidato. score(u,p,DECAY) = suma(DECAY^d) sobre pedidos prior de u
# que contienen p, dividido por la misma suma sobre TODOS los pedidos prior
# de u (d = order_number del último prior de u, menos order_number del
# pedido). Con DECAY=1.0 es frecuencia pura.

# --- N8: rutinarios vs exploradores ------------------------------------------
W_N8_DEFAULT = 5


def _sql_construir_n8(w: int) -> str:
    # Medido en vivo: el conteo de productos_distintos en la ventana de los
    # primeros W pedidos tarda ~2.9s y la tasa de recompra del historial
    # completo por usuario ~2.2s; juntas (más el resto de N8) se acercan o
    # superan el objetivo de 5s. Se precalculan ambas de una vez.
    return f"""
    CREATE TABLE agg_usuario AS
    WITH historial AS (
        SELECT o.user_id, COUNT(*) AS lineas_totales,
               SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) AS lineas_recompra_totales
        FROM src.order_products op JOIN src.orders o ON o.order_id = op.order_id
        GROUP BY o.user_id
    ),
    pedidos_hist AS (
        SELECT user_id, COUNT(*) AS pedidos_totales
        FROM src.orders WHERE eval_set IN ('prior', 'train') GROUP BY user_id
    ),
    intervalo_hist AS (
        SELECT user_id, MEDIAN(days_since_prior_order) AS mediana_intervalo
        FROM src.orders WHERE days_since_prior_order IS NOT NULL GROUP BY user_id
    ),
    ventana AS (
        SELECT o.user_id, COUNT(DISTINCT op.product_id) AS productos_distintos_ventana,
               COUNT(*) AS lineas_ventana,
               SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) AS lineas_recompra_ventana,
               SUM(CASE WHEN op.reordered = 0 THEN 1 ELSE 0 END) AS lineas_nuevas_ventana,
               COUNT(DISTINCT o.order_id) AS pedidos_en_ventana
        FROM src.order_products op JOIN src.orders o ON o.order_id = op.order_id
        WHERE o.eval_set IN ('prior', 'train') AND o.order_number <= {w}
        GROUP BY o.user_id
    )
    SELECT p.user_id, p.pedidos_totales,
           COALESCE(h.lineas_totales, 0) AS lineas_totales,
           COALESCE(h.lineas_recompra_totales, 0) AS lineas_recompra_totales,
           CASE WHEN COALESCE(h.lineas_totales, 0) > 0
                THEN h.lineas_recompra_totales * 1.0 / h.lineas_totales ELSE NULL END AS tasa_recompra_historial,
           ih.mediana_intervalo,
           v.pedidos_en_ventana, v.productos_distintos_ventana, v.lineas_ventana,
           v.lineas_recompra_ventana, v.lineas_nuevas_ventana
    FROM pedidos_hist p
    LEFT JOIN historial h ON h.user_id = p.user_id
    LEFT JOIN intervalo_hist ih ON ih.user_id = p.user_id
    LEFT JOIN ventana v ON v.user_id = p.user_id;
    """


DECAYS_N11 = (1.0, 0.9, 0.8, 0.7)
K_RANK_MAX_N11 = 20  # el mayor K de {5,10,20} que evalúa N11


def _sql_construir_n11() -> str:
    # Medido: rankear 8.47M filas de agg_n11_score con ROW_NUMBER() OVER(...)
    # 4 veces (una por DECAY candidato) EN VIVO, más las métricas de 4
    # métodos x 3 K, mide ~15s -- muy por encima de 5s. Se materializa el
    # top-20 (el mayor K evaluado) por usuario y DECAY una sola vez aquí; en
    # vivo, N11 solo filtra `rango <= k` sobre esta tabla ya chica.
    columnas_decay = ", ".join(f"d{int(d*10)}" for d in DECAYS_N11)
    numeradores = ",\n           ".join(
        f"SUM(POWER({d}, cp.d)) AS num_d{int(d*10)}" for d in DECAYS_N11
    )
    denominadores = ",\n           ".join(
        f"SUM(POWER({d}, dp.d)) AS den_d{int(d*10)}" for d in DECAYS_N11
    )
    scores = ",\n           ".join(
        f"n.num_d{int(d*10)} / den.den_d{int(d*10)} AS score_d{int(d*10)}" for d in DECAYS_N11
    )
    rank_union = "\n    UNION ALL\n    ".join(
        f"""SELECT user_id, product_id, ultima_compra_k, '{int(d*10)}' AS decay_x10,
               ROW_NUMBER() OVER (PARTITION BY user_id
                   ORDER BY score_d{int(d*10)} DESC, ultima_compra_k DESC, product_id ASC) AS rango
        FROM agg_n11_score
        QUALIFY rango <= {K_RANK_MAX_N11}"""
        for d in DECAYS_N11
    )
    return f"""
    CREATE TEMP TABLE n11_usuarios AS
    SELECT o.user_id, MAX(o.order_number) - 1 AS ultimo_prior_k
    FROM src.orders o WHERE o.eval_set = 'train'
    GROUP BY o.user_id;

    CREATE TEMP TABLE n11_compras_prior AS
    SELECT o.user_id, op.product_id, o.order_number, o.order_id,
           (u.ultimo_prior_k - o.order_number) AS d
    FROM src.order_products op
    JOIN src.orders o ON o.order_id = op.order_id
    JOIN n11_usuarios u ON u.user_id = o.user_id
    WHERE o.eval_set = 'prior';

    CREATE TEMP TABLE n11_num AS
    SELECT user_id, product_id,
           MAX(order_number) AS ultima_compra_k,
           COUNT(*) AS n_compras,
           {numeradores}
    FROM n11_compras_prior cp
    GROUP BY user_id, product_id;

    CREATE TEMP TABLE n11_den AS
    SELECT dp.user_id,
           {denominadores}
    FROM n11_compras_prior dp
    GROUP BY dp.user_id;

    CREATE TABLE agg_n11_score AS
    SELECT n.user_id, n.product_id, n.ultima_compra_k, n.n_compras,
           {scores}
    FROM n11_num n JOIN n11_den den ON den.user_id = n.user_id;

    CREATE TABLE agg_n11_usuarios AS SELECT * FROM n11_usuarios;

    CREATE TABLE agg_n11_rank AS
    {rank_union};

    DROP TABLE n11_compras_prior;
    DROP TABLE n11_num;
    DROP TABLE n11_den;
    """


def construir_agregados(
    source_path: Path,
    agg_path: Path,
    *,
    min_soporte_producto: float = MIN_SOPORTE_PRODUCTO_DEFAULT,
    min_pares_lift: int = MIN_PARES_LIFT_DEFAULT,
    k_post: int = K_POST_DEFAULT,
    min_tam_n10: int = MIN_TAM_N10_DEFAULT,
    w_n8: int = W_N8_DEFAULT,
) -> dict:
    """Construye `agg_path` desde cero (agg_pares para N2, agg_n1_producto
    para N1, agg_pedido para N7/N8/N9, agg_n10_producto para N10,
    agg_n11_score/agg_n11_usuarios para N11). Atómico: temp file + rename al final."""
    tmp_path = agg_path.parent / f"{agg_path.name}.building-{os.getpid()}"
    if tmp_path.exists():
        tmp_path.unlink()
    agg_path.parent.mkdir(parents=True, exist_ok=True)

    con_source = duckdb.connect(str(source_path), read_only=True)
    huella = huella_bd(con_source)
    total_pedidos_lineas = con_source.execute(
        "SELECT COUNT(*) FROM orders WHERE eval_set IN ('prior', 'train')"
    ).fetchone()[0]
    con_source.close()

    min_pedidos_candidato = int(min_soporte_producto * total_pedidos_lineas)

    con = duckdb.connect(str(tmp_path))
    try:
        con.execute(f"SET memory_limit='{MEMORY_LIMIT}'")
        con.execute(f"ATTACH '{source_path.as_posix()}' AS src (READ_ONLY)")

        t0 = time.perf_counter()
        con.execute(_sql_construir_pares(min_pedidos_candidato))
        dur_n2 = time.perf_counter() - t0
        n_candidatos = con.execute("SELECT COUNT(*) FROM agg_candidatos").fetchone()[0]
        cand_pp = con.execute("SELECT SUM(pedidos_con_producto) FROM agg_candidatos").fetchone()[0]
        total_pp = con.execute(
            "SELECT SUM(pedidos_con_producto) FROM ("
            "  SELECT op.product_id, COUNT(DISTINCT op.order_id) AS pedidos_con_producto"
            "  FROM src.order_products op JOIN src.orders o ON o.order_id = op.order_id"
            "  WHERE o.eval_set IN ('prior','train') GROUP BY op.product_id"
            ")"
        ).fetchone()[0]
        n_pares = con.execute("SELECT COUNT(*) FROM agg_pares").fetchone()[0]

        t0 = time.perf_counter()
        con.execute(_sql_construir_n1(k_post))
        dur_n1 = time.perf_counter() - t0
        n1_productos = con.execute("SELECT COUNT(*) FROM agg_n1_producto").fetchone()[0]

        t0 = time.perf_counter()
        con.execute(_SQL_PEDIDO)
        dur_pedido = time.perf_counter() - t0
        n_pedidos = con.execute("SELECT COUNT(*) FROM agg_pedido").fetchone()[0]

        t0 = time.perf_counter()
        con.execute(_SQL_N7)
        dur_n7 = time.perf_counter() - t0
        n7_filas_k = con.execute("SELECT COUNT(*) FROM agg_n7_por_k").fetchone()[0]

        t0 = time.perf_counter()
        con.execute(_sql_construir_n10(min_tam_n10))
        dur_n10 = time.perf_counter() - t0
        n10_productos = con.execute("SELECT COUNT(*) FROM agg_n10_producto").fetchone()[0]

        t0 = time.perf_counter()
        con.execute(_sql_construir_n8(w_n8))
        dur_n8 = time.perf_counter() - t0
        n8_usuarios = con.execute("SELECT COUNT(*) FROM agg_usuario").fetchone()[0]

        t0 = time.perf_counter()
        con.execute(_sql_construir_n11())
        dur_n11 = time.perf_counter() - t0
        n11_filas = con.execute("SELECT COUNT(*) FROM agg_n11_score").fetchone()[0]
        n11_usuarios = con.execute("SELECT COUNT(*) FROM agg_n11_usuarios").fetchone()[0]

        con.execute("DETACH src")

        meta = {
            "n2_min_soporte_producto": str(min_soporte_producto),
            "n2_min_pedidos_candidato": str(min_pedidos_candidato),
            "n2_min_pares_lift": str(min_pares_lift),
            "n2_n_candidatos": str(n_candidatos),
            "n2_n_pares": str(n_pares),
            "n2_pct_cobertura_pedidos_producto": f"{(cand_pp / total_pp) if total_pp else 0:.6f}",
            "n2_duracion_construccion_s": f"{dur_n2:.3f}",
            "n1_k_post": str(k_post),
            "n1_n_productos": str(n1_productos),
            "n1_duracion_construccion_s": f"{dur_n1:.3f}",
            "pedido_n_filas": str(n_pedidos),
            "pedido_duracion_construccion_s": f"{dur_pedido:.3f}",
            "n7_filas_k": str(n7_filas_k),
            "n7_duracion_construccion_s": f"{dur_n7:.3f}",
            "n10_min_tam": str(min_tam_n10),
            "n10_n_productos": str(n10_productos),
            "n10_duracion_construccion_s": f"{dur_n10:.3f}",
            "n8_w": str(w_n8),
            "n8_n_usuarios": str(n8_usuarios),
            "n8_duracion_construccion_s": f"{dur_n8:.3f}",
            "n11_decays": ",".join(str(d) for d in DECAYS_N11),
            "n11_n_filas": str(n11_filas),
            "n11_n_usuarios": str(n11_usuarios),
            "n11_duracion_construccion_s": f"{dur_n11:.3f}",
            "total_pedidos_con_lineas": str(total_pedidos_lineas),
            "version_duckdb": version_duckdb(),
            "fecha_generacion": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "huella_orders_total": str(huella["orders_total"]),
            "huella_orders_test": str(huella["orders_test"]),
            "huella_order_products_prior": str(huella["order_products_prior"]),
            "huella_order_products_train": str(huella["order_products_train"]),
        }
        con.execute("CREATE TABLE agg_meta (clave VARCHAR PRIMARY KEY, valor VARCHAR)")
        con.executemany("INSERT INTO agg_meta VALUES (?, ?)", list(meta.items()))
        con.close()
    except Exception:
        con.close()
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    os.replace(tmp_path, agg_path)
    return {
        "n2_n_candidatos": n_candidatos,
        "n2_n_pares": n_pares,
        "n2_duracion_s": dur_n2,
        "n1_n_productos": n1_productos,
        "n1_duracion_s": dur_n1,
        "min_pedidos_candidato": min_pedidos_candidato,
        "pedido_n_filas": n_pedidos,
        "pedido_duracion_s": dur_pedido,
        "n7_filas_k": n7_filas_k,
        "n7_duracion_s": dur_n7,
        "n10_n_productos": n10_productos,
        "n10_duracion_s": dur_n10,
        "n8_n_usuarios": n8_usuarios,
        "n8_duracion_s": dur_n8,
        "n11_n_filas": n11_filas,
        "n11_n_usuarios": n11_usuarios,
        "n11_duracion_s": dur_n11,
    }


def abrir_agregado(agg_path: Path, con_source: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Abre `agg_path` de solo lectura y valida que su huella coincida con `con_source`.

    Lanza `AgregadoRancioError` si no coincide (hay que --rebuild), o
    `FileNotFoundError` si el agregado no existe todavía.
    """
    if not agg_path.exists():
        raise FileNotFoundError(
            f"No existe {agg_path}. Corre: python -m src.analytics.agregados --rebuild"
        )

    con_agg = duckdb.connect(str(agg_path), read_only=True)
    con_agg.execute(f"SET memory_limit='{MEMORY_LIMIT}'")

    meta = dict(con_agg.execute("SELECT clave, valor FROM agg_meta").fetchall())
    huella_actual = huella_bd(con_source)
    huella_guardada = {
        "orders_total": int(meta["huella_orders_total"]),
        "orders_test": int(meta["huella_orders_test"]),
        "order_products_prior": int(meta["huella_order_products_prior"]),
        "order_products_train": int(meta["huella_order_products_train"]),
    }
    if huella_actual != huella_guardada:
        con_agg.close()
        raise AgregadoRancioError(
            f"{agg_path} está rancio: huella guardada {huella_guardada} != "
            f"huella actual de la BD fuente {huella_actual}. Corre --rebuild."
        )
    return con_agg


# ---------------------------------------------------------------------------
# Medición aislada de un solo valor de MIN_SOPORTE_PRODUCTO (uso interno,
# invocado como subproceso por --tabla-compromiso para que el pico de RSS de
# cada valor se mida en un proceso nuevo, no acumulado con los anteriores).
# ---------------------------------------------------------------------------

def _medir_un_valor(source_path: Path, min_soporte_producto: float) -> dict:
    con_source = duckdb.connect(str(source_path), read_only=True)
    total_pedidos_lineas = con_source.execute(
        "SELECT COUNT(*) FROM orders WHERE eval_set IN ('prior', 'train')"
    ).fetchone()[0]
    con_source.close()
    min_pedidos_candidato = int(min_soporte_producto * total_pedidos_lineas)

    t0 = time.perf_counter()
    con = duckdb.connect(":memory:")
    con.execute(f"SET memory_limit='{MEMORY_LIMIT}'")
    con.execute(f"ATTACH '{source_path.as_posix()}' AS src (READ_ONLY)")
    con.execute(_sql_construir_pares(min_pedidos_candidato))
    n_candidatos, cand_pp = con.execute(
        "SELECT COUNT(*), SUM(pedidos_con_producto) FROM agg_candidatos"
    ).fetchone()
    total_pp = con.execute(
        "SELECT SUM(pedidos_con_producto) FROM ("
        "  SELECT op.product_id, COUNT(DISTINCT op.order_id) AS pedidos_con_producto"
        "  FROM src.order_products op JOIN src.orders o ON o.order_id = op.order_id"
        "  WHERE o.eval_set IN ('prior','train') GROUP BY op.product_id"
        ")"
    ).fetchone()[0]
    n_pares = con.execute("SELECT COUNT(*) FROM agg_pares").fetchone()[0]
    con.close()
    duracion = time.perf_counter() - t0

    return {
        "min_soporte_producto": min_soporte_producto,
        "min_pedidos_candidato": min_pedidos_candidato,
        "n_candidatos": n_candidatos,
        "pct_cobertura_pedidos_producto": cand_pp / total_pp,
        "n_pares": n_pares,
        "duracion_s": duracion,
    }


def tabla_compromiso(source_path: Path, valores: list[float]) -> list[dict]:
    """Corre `_medir_un_valor` para cada valor EN UN PROCESO NUEVO (subprocess)
    para que el pico de RSS de cada uno se mida aislado, no acumulado."""
    filas = []
    for v in valores:
        cmd = [sys.executable, "-m", "src.analytics.agregados", "--medir", str(v), "--db", str(source_path)]
        proc = subprocess.run(
            ["/usr/bin/time", "-v", *cmd],
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[2],
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Falló --medir {v}: {proc.stderr[-2000:]}")
        salida = json.loads(proc.stdout.strip().splitlines()[-1])
        rss_kb = None
        for linea in proc.stderr.splitlines():
            if "Maximum resident set size" in linea:
                rss_kb = int(linea.split(":")[1].strip())
        salida["rss_mb"] = rss_kb / 1024 if rss_kb else None
        filas.append(salida)
    return filas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.analytics.agregados")
    parser.add_argument("--db", default="data/instacart.duckdb")
    parser.add_argument("--out", default="data/instacart_agg.duckdb")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--min-soporte-producto", type=float, default=MIN_SOPORTE_PRODUCTO_DEFAULT)
    parser.add_argument("--min-pares-lift", type=int, default=MIN_PARES_LIFT_DEFAULT)
    parser.add_argument("--k-post", type=int, default=K_POST_DEFAULT)
    parser.add_argument("--min-tam-n10", type=int, default=MIN_TAM_N10_DEFAULT)
    parser.add_argument("--w-n8", type=int, default=W_N8_DEFAULT)
    parser.add_argument("--medir", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--tabla-compromiso", action="store_true")
    parser.add_argument("--valores", default="0.0005,0.001,0.002,0.003")
    args = parser.parse_args(argv)

    source_path = Path(args.db)

    if args.medir is not None:
        resultado = _medir_un_valor(source_path, args.medir)
        print(json.dumps(resultado))
        return 0

    if args.tabla_compromiso:
        valores = [float(v) for v in args.valores.split(",")]
        filas = tabla_compromiso(source_path, valores)
        print(f"{'MIN_SOPORTE_PRODUCTO':>22} {'min_pedidos':>12} {'candidatos':>11} "
              f"{'%cobertura':>11} {'pares':>10} {'tiempo_s':>9} {'RSS_MB':>9}")
        for f in filas:
            print(f"{f['min_soporte_producto']:>22} {f['min_pedidos_candidato']:>12} "
                  f"{f['n_candidatos']:>11} {f['pct_cobertura_pedidos_producto']*100:>10.2f}% "
                  f"{f['n_pares']:>10} {f['duracion_s']:>9.2f} "
                  f"{(f['rss_mb'] or 0):>9.1f}")
        return 0

    out_path = Path(args.out)
    if out_path.exists() and not args.rebuild:
        print(f"{out_path} ya existe. Usa --rebuild para reconstruirlo.")
        return 0
    if out_path.exists():
        out_path.unlink()

    resultado = construir_agregados(
        source_path, out_path,
        min_soporte_producto=args.min_soporte_producto,
        min_pares_lift=args.min_pares_lift,
        k_post=args.k_post,
        min_tam_n10=args.min_tam_n10,
        w_n8=args.w_n8,
    )
    print(f"agg_candidatos: {resultado['n2_n_candidatos']} productos "
          f"(min_pedidos_candidato={resultado['min_pedidos_candidato']})")
    print(f"agg_pares: {resultado['n2_n_pares']} pares en {resultado['n2_duracion_s']:.2f}s")
    print(f"agg_n1_producto: {resultado['n1_n_productos']} productos en {resultado['n1_duracion_s']:.2f}s")
    print(f"agg_pedido: {resultado['pedido_n_filas']} pedidos en {resultado['pedido_duracion_s']:.2f}s")
    print(f"agg_n7_por_k/pareado/reordered_k1: {resultado['n7_filas_k']} valores de k en {resultado['n7_duracion_s']:.2f}s")
    print(f"agg_n10_producto: {resultado['n10_n_productos']} productos en {resultado['n10_duracion_s']:.2f}s")
    print(f"agg_n11_score: {resultado['n11_n_filas']} filas ({resultado['n11_n_usuarios']} usuarios) "
          f"en {resultado['n11_duracion_s']:.2f}s")
    print(f"agg_usuario: {resultado['n8_n_usuarios']} usuarios en {resultado['n8_duracion_s']:.2f}s")
    print(f"Listo: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
