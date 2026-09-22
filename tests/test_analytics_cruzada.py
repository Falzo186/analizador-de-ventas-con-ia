"""C3 (Fase 2B): verificación cruzada de los agregados contra un cálculo en
vivo (sin agregado) sobre el dataset REAL completo.

- `agg_n1_producto` vs. pipeline en vivo para 30 productos: los 15 de mayor
  volumen (líneas desc, desempate product_id asc) y los 15 "más elegidos"
  que no estén ya en esa lista (usuarios_distintos desc, desempate
  product_id asc) — regla 100% determinista, sin aleatoriedad.
- `agg_pares` vs. recuento directo desde order_products para 10 pares top
  por pedidos_con_par y 10 top por lift (desempates explícitos).

Tolerancia: enteros exactos (0), floats 1e-9. Se saltan si faltan
data/instacart.duckdb o data/instacart_agg.duckdb.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

RAIZ = Path(__file__).resolve().parents[1]
DB_PATH = RAIZ / "data" / "instacart.duckdb"
AGG_PATH = RAIZ / "data" / "instacart_agg.duckdb"

pytestmark = pytest.mark.integration

_FALTAN = not (DB_PATH.exists() and AGG_PATH.exists())
_RAZON = f"Requiere {DB_PATH.name} y {AGG_PATH.name}"

TOL_FLOAT = 1e-9


@pytest.fixture(scope="module")
def con():
    if _FALTAN:
        pytest.skip(_RAZON)
    c = duckdb.connect(str(DB_PATH), read_only=True)
    c.execute("SET memory_limit='4GB'")
    c.execute("SET threads=1")
    c.execute(f"ATTACH '{AGG_PATH.as_posix()}' AS agg (READ_ONLY)")
    yield c
    c.close()


@pytest.mark.skipif(_FALTAN, reason=_RAZON)
def test_agg_n1_producto_vs_pipeline_en_vivo(con):
    k_post = int(dict(con.execute("SELECT clave, valor FROM agg.agg_meta").fetchall())["n1_k_post"])

    # Regla determinista de selección de los 30 productos (sin aleatoriedad):
    top_volumen = [
        r[0] for r in con.execute(
            "SELECT product_id FROM agg.agg_n1_producto ORDER BY lineas DESC, product_id ASC LIMIT 15"
        ).fetchall()
    ]
    top_elegidos = [
        r[0] for r in con.execute(
            "SELECT product_id FROM agg.agg_n1_producto "
            f"WHERE product_id NOT IN ({','.join(map(str, top_volumen))}) "
            "ORDER BY usuarios_distintos DESC, product_id ASC LIMIT 15"
        ).fetchall()
    ]
    productos = top_volumen + top_elegidos
    assert len(set(productos)) == 30
    lista_sql = ",".join(map(str, productos))

    # --- cálculo EN VIVO, sin usar agg_n1_producto ---
    base_viva = dict(
        (pid, (lineas, lineas_recompra))
        for pid, lineas, lineas_recompra in con.execute(
            f"SELECT product_id, COUNT(*), SUM(CASE WHEN reordered=1 THEN 1 ELSE 0 END) "
            f"FROM order_products WHERE product_id IN ({lista_sql}) GROUP BY product_id"
        ).fetchall()
    )
    usuarios_vivo = dict(con.execute(
        f"SELECT op.product_id, COUNT(DISTINCT o.user_id) FROM order_products op "
        f"JOIN orders o ON o.order_id = op.order_id "
        f"WHERE op.product_id IN ({lista_sql}) GROUP BY op.product_id"
    ).fetchall())

    abandono_vivo = dict(
        (pid, (obs, aband))
        for pid, obs, aband in con.execute(f"""
        WITH lineas_usuario AS (
            SELECT user_id, order_number,
                   SUM(1) OVER (PARTITION BY user_id ORDER BY order_number
                                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS acumulado
            FROM orders WHERE eval_set IN ('prior', 'train')
        ),
        total_usuario AS (
            SELECT user_id, COUNT(*) AS n_total FROM orders
            WHERE eval_set IN ('prior', 'train') GROUP BY user_id
        ),
        compras AS (
            SELECT o.user_id, op.product_id, MIN(o.order_number) AS f, COUNT(*) AS n_compras
            FROM order_products op JOIN orders o ON o.order_id = op.order_id
            WHERE o.eval_set IN ('prior', 'train') AND op.product_id IN ({lista_sql})
            GROUP BY o.user_id, op.product_id
        ),
        j AS (
            SELECT c.product_id, (tu.n_total - lu.acumulado) AS n_posteriores, c.n_compras
            FROM compras c
            JOIN lineas_usuario lu ON lu.user_id = c.user_id AND lu.order_number = c.f
            JOIN total_usuario tu ON tu.user_id = c.user_id
        )
        SELECT product_id,
               SUM(CASE WHEN n_posteriores >= {k_post} THEN 1 ELSE 0 END),
               SUM(CASE WHEN n_posteriores >= {k_post} AND n_compras = 1 THEN 1 ELSE 0 END)
        FROM j GROUP BY product_id
        """).fetchall()
    )

    agg_rows = con.execute(
        f"SELECT product_id, lineas, usuarios_distintos, lineas_recompra, tasa_recompra, "
        f"observables, abandonos, tasa_abandono FROM agg.agg_n1_producto "
        f"WHERE product_id IN ({lista_sql})"
    ).fetchall()
    assert len(agg_rows) == 30

    for pid, lineas, usu, lineas_r, tasa_r, obs, aband, tasa_a in agg_rows:
        lineas_v, lineas_r_v = base_viva[pid]
        usu_v = usuarios_vivo[pid]
        obs_v, aband_v = abandono_vivo.get(pid, (0, 0))

        assert lineas == lineas_v, f"product {pid}: lineas {lineas} != vivo {lineas_v}"
        assert usu == usu_v, f"product {pid}: usuarios_distintos {usu} != vivo {usu_v}"
        assert lineas_r == lineas_r_v, f"product {pid}: lineas_recompra {lineas_r} != vivo {lineas_r_v}"
        assert obs == obs_v, f"product {pid}: observables {obs} != vivo {obs_v}"
        assert aband == aband_v, f"product {pid}: abandonos {aband} != vivo {aband_v}"

        assert abs(tasa_r - (lineas_r_v / lineas_v)) < TOL_FLOAT
        if obs_v > 0:
            assert abs(tasa_a - (aband_v / obs_v)) < TOL_FLOAT
        else:
            assert tasa_a is None


@pytest.mark.skipif(_FALTAN, reason=_RAZON)
def test_agg_pares_vs_recuento_directo(con):
    top_frecuentes = con.execute(
        "SELECT producto_a, producto_b, pedidos_con_par, confianza_a_b, confianza_b_a, lift "
        "FROM agg.agg_pares ORDER BY pedidos_con_par DESC, producto_a ASC, producto_b ASC LIMIT 10"
    ).fetchall()
    top_lift = con.execute(
        "SELECT producto_a, producto_b, pedidos_con_par, confianza_a_b, confianza_b_a, lift "
        "FROM agg.agg_pares ORDER BY lift DESC, pedidos_con_par DESC, producto_a ASC, producto_b ASC LIMIT 10"
    ).fetchall()

    total_pedidos_con_lineas = con.execute(
        "SELECT COUNT(*) FROM orders WHERE eval_set IN ('prior', 'train')"
    ).fetchone()[0]

    for pa, pb, pedidos_con_par, conf_ab, conf_ba, lift in top_frecuentes + top_lift:
        n_par = con.execute(
            "SELECT COUNT(*) FROM order_products a JOIN order_products b "
            "ON a.order_id = b.order_id WHERE a.product_id = ? AND b.product_id = ?",
            [pa, pb],
        ).fetchone()[0]
        n_a = con.execute(
            "SELECT COUNT(DISTINCT order_id) FROM order_products WHERE product_id = ?", [pa]
        ).fetchone()[0]
        n_b = con.execute(
            "SELECT COUNT(DISTINCT order_id) FROM order_products WHERE product_id = ?", [pb]
        ).fetchone()[0]

        assert n_par == pedidos_con_par, f"par ({pa},{pb}): pedidos_con_par {pedidos_con_par} != directo {n_par}"
        assert abs(conf_ab - n_par / n_a) < TOL_FLOAT
        assert abs(conf_ba - n_par / n_b) < TOL_FLOAT
        lift_directo = (n_par * total_pedidos_con_lineas) / (n_a * n_b)
        assert abs(lift - lift_directo) < TOL_FLOAT
