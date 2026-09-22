"""Invariantes de reconciliación de N7-N11 contra el dataset REAL completo
(Fase 2B). Un test por invariante listado en el prompt de esta fase.

Se saltan si `data/instacart.duckdb` o `data/instacart_agg.duckdb` no
existen. Marcados @pytest.mark.integration.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.analytics.conexion import abrir_conexion
from src.analytics.registro import invocar

RAIZ = Path(__file__).resolve().parents[1]
DB_PATH = RAIZ / "data" / "instacart.duckdb"
AGG_PATH = RAIZ / "data" / "instacart_agg.duckdb"

pytestmark = pytest.mark.integration

_FALTAN = not (DB_PATH.exists() and AGG_PATH.exists())
_RAZON = f"Requiere {DB_PATH.name} y {AGG_PATH.name}"


@pytest.fixture(scope="module")
def con():
    if _FALTAN:
        pytest.skip(_RAZON)
    c = abrir_conexion(DB_PATH)
    yield c
    c.close()


@pytest.mark.skipif(_FALTAN, reason=_RAZON)
def test_n7_suma_pedidos_y_lineas(con):
    # Suma de pedidos por k (TODOS los k, no solo los mostrados) y suma de
    # líneas, verificadas independientemente contra orders/order_products.
    esperado_pedidos = con.execute(
        "SELECT COUNT(*) FROM orders WHERE eval_set IN ('prior','train')"
    ).fetchone()[0]
    esperado_lineas = con.execute("SELECT COUNT(*) FROM order_products").fetchone()[0]

    con_agg = duckdb.connect(str(AGG_PATH), read_only=True)
    total_pedidos = con_agg.execute("SELECT SUM(pedidos) FROM agg_n7_por_k").fetchone()[0]
    total_lineas = con_agg.execute("SELECT SUM(tamano) FROM agg_pedido").fetchone()[0]
    con_agg.close()

    assert total_pedidos == esperado_pedidos == 3_346_083
    assert total_lineas == esperado_lineas == 33_819_106


@pytest.mark.skipif(_FALTAN, reason=_RAZON)
def test_n8_clasificados_mas_excluidos(con):
    esperado_total = con.execute("SELECT COUNT(DISTINCT user_id) FROM orders").fetchone()[0]
    res = invocar("N8", con, AGG_PATH)
    vc = {v.etiqueta: v.valor for v in res.valores_clave}
    assert vc["Usuarios clasificables"] + vc["Usuarios excluidos por W"] == esperado_total == 206_209


@pytest.mark.skipif(_FALTAN, reason=_RAZON)
def test_n9_histograma_y_suma_tamano(con):
    esperado_pedidos = con.execute(
        "SELECT COUNT(*) FROM orders WHERE eval_set IN ('prior','train')"
    ).fetchone()[0]
    esperado_lineas = con.execute("SELECT COUNT(*) FROM order_products").fetchone()[0]
    res = invocar("N9", con, AGG_PATH)
    suma_hist = sum(f["pedidos"] for f in res.tablas["histograma_tamano"])
    assert suma_hist == esperado_pedidos == 3_346_083

    con_agg = duckdb.connect(str(AGG_PATH), read_only=True)
    suma_tamano = con_agg.execute("SELECT SUM(tamano) FROM agg_pedido").fetchone()[0]
    con_agg.close()
    assert suma_tamano == esperado_lineas == 33_819_106


@pytest.mark.skipif(_FALTAN, reason=_RAZON)
def test_n10_incluidas_mas_excluidas(con):
    esperado = con.execute("SELECT COUNT(*) FROM order_products").fetchone()[0]
    res = invocar("N10", con, AGG_PATH)
    vc = {v.etiqueta: v.valor for v in res.valores_clave}
    assert vc["Líneas incluidas"] + vc["Líneas excluidas (MIN_TAM)"] == esperado == 33_819_106


@pytest.mark.skipif(_FALTAN, reason=_RAZON)
def test_n11_usuarios_y_lineas_objetivo(con):
    esperado_usuarios = con.execute(
        "SELECT COUNT(DISTINCT user_id) FROM orders WHERE eval_set = 'train'"
    ).fetchone()[0]
    esperado_lineas = con.execute(
        "SELECT COUNT(*) FROM orders o JOIN order_products op ON op.order_id = o.order_id "
        "WHERE o.eval_set = 'train'"
    ).fetchone()[0]

    res = invocar("N11", con, AGG_PATH)
    vc = {v.etiqueta: v.valor for v in res.valores_clave}
    assert vc["Usuarios evaluados"] == esperado_usuarios == 131_209

    # "evaluados" (80%) + "excluidos" (20% de validación) = universo total.
    fila_eval_k10 = next(f for f in res.tablas["metricas"]
                          if f["metodo"] == "baseline_decay_recencia" and f["k"] == 10)
    fila_val_k10 = next(f for f in res.tablas["metricas"]
                         if f["metodo"] == "baseline_decay_recencia_EN_VALIDACION" and f["k"] == 10)
    assert fila_eval_k10["n_usuarios"] + fila_val_k10["n_usuarios"] == esperado_usuarios

    assert esperado_lineas == 1_384_617
