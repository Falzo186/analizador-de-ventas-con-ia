"""Prueba el cargador y el validador de Instacart sobre una fixture pequeña.

`tests/fixtures/instacart_mini/`: 5 usuarios, 20 pedidos, incluye un pedido
'test' (sin líneas), el primer pedido de cada usuario con
`days_since_prior_order` NULL, y un producto con pasillo 'missing'
(product_id=6) y otro con departamento 'missing' (product_id=7).

Todas las cifras esperadas en este archivo se calcularon A MANO contando las
líneas de `tests/fixtures/instacart_mini/*.csv` (el detalle del conteo está
en los comentarios de cada test), no se copiaron de ninguna salida del
programa.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.db.instacart import construir_duckdb, main as instacart_main
from src.db.instacart_validacion import (
    check_rangos,
    hay_fallos,
    validar_completo,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "instacart_mini"

# Calculado a mano sobre orders.csv / order_products__{prior,train}.csv de la fixture.
ESPERADOS_FIXTURE = {
    "orders": 20,
    "order_products__prior": 43,
    "order_products__train": 5,
}
ORDERS_TEST_ESPERADO_FIXTURE = 1


@pytest.fixture()
def db_fixture(tmp_path) -> Path:
    out = tmp_path / "instacart_mini.duckdb"
    construir_duckdb(FIXTURE_DIR, out)
    return out


def test_carga_conteos_por_tabla(db_fixture):
    con = duckdb.connect(str(db_fixture), read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM aisles").fetchone()[0] == 5
        assert con.execute("SELECT COUNT(*) FROM departments").fetchone()[0] == 4
        assert con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 8
        assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 20
        assert con.execute("SELECT COUNT(*) FROM order_products__prior").fetchone()[0] == 43
        assert con.execute("SELECT COUNT(*) FROM order_products__train").fetchone()[0] == 5
        # Vista = UNION ALL de las dos tablas físicas.
        assert con.execute("SELECT COUNT(*) FROM order_products").fetchone()[0] == 48
    finally:
        con.close()


def test_tasa_recompra_producto_calculada_a_mano(db_fixture):
    # Producto 1 (Banana) aparece en 6 líneas de order_products__prior
    # (pedidos 101 reordered=0, 102 reordered=1, 104 reordered=1, 203
    # reordered=0, 401 reordered=0, 402 reordered=1) y 2 de
    # order_products__train (204 reordered=1, 404 reordered=1).
    # Total: 8 líneas, 5 con reordered=1 -> tasa = 5/8 = 0.625.
    con = duckdb.connect(str(db_fixture), read_only=True)
    try:
        tasa, n = con.execute(
            "SELECT SUM(CASE WHEN reordered = 1 THEN 1 ELSE 0 END)::DOUBLE / COUNT(*), COUNT(*) "
            "FROM order_products WHERE product_id = 1"
        ).fetchone()
    finally:
        con.close()
    assert n == 8
    assert tasa == pytest.approx(0.625)


def test_pedidos_por_dia_hora_calculado_a_mano(db_fixture):
    # De las 20 filas de orders.csv, (order_dow=0, order_hour_of_day=8)
    # aparece dos veces (pedidos 101 y 303) y (order_dow=1, order_hour_of_day=9)
    # también dos veces (pedidos 102 y 304); las otras 16 combinaciones son
    # únicas. Total de grupos: 20 - 2 = 18 (dos pares de filas se colapsan en
    # un grupo cada uno); la suma de los conteos debe seguir dando 20.
    con = duckdb.connect(str(db_fixture), read_only=True)
    try:
        filas = con.execute(
            "SELECT order_dow, order_hour_of_day, COUNT(*) AS n "
            "FROM orders GROUP BY order_dow, order_hour_of_day"
        ).fetchall()
    finally:
        con.close()
    por_par = {(dow, hora): n for dow, hora, n in filas}
    assert por_par[(0, 8)] == 2
    assert por_par[(1, 9)] == 2
    assert len(filas) == 18
    assert sum(n for _, _, n in filas) == 20


def test_variedad_por_usuario_calculada_a_mano(db_fixture):
    # Usuario 1: pedidos 101-104 (prior, 105 es 'test' y no tiene líneas).
    # Líneas: 101 tiene 3, 102 tiene 3, 103 tiene 2, 104 tiene 4 -> 12 líneas.
    # Productos distintos usados: {1,2,4} ∪ {1,4,3} ∪ {2,5} ∪ {1,8,6,3}
    # = {1,2,3,4,5,6,8} -> 7 productos distintos.
    con = duckdb.connect(str(db_fixture), read_only=True)
    try:
        lineas, distintos = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT op.product_id) "
            "FROM order_products op JOIN orders o ON o.order_id = op.order_id "
            "WHERE o.user_id = 1"
        ).fetchone()
    finally:
        con.close()
    assert lineas == 12
    assert distintos == 7


def test_cli_construye_y_es_idempotente(tmp_path, capsys):
    out = tmp_path / "cli.duckdb"
    rc = instacart_main(["--raw", str(FIXTURE_DIR), "--out", str(out)])
    assert rc == 0
    assert out.exists()

    # Sin --rebuild, correrlo de nuevo sobre un archivo existente no hace nada.
    rc2 = instacart_main(["--raw", str(FIXTURE_DIR), "--out", str(out)])
    assert rc2 == 0
    assert "ya existe" in capsys.readouterr().out


def test_validador_pasa_sobre_datos_limpios(db_fixture):
    con = duckdb.connect(str(db_fixture), read_only=True)
    try:
        resultados = validar_completo(
            con, FIXTURE_DIR,
            esperados=ESPERADOS_FIXTURE,
            orders_test_esperado=ORDERS_TEST_ESPERADO_FIXTURE,
        )
    finally:
        con.close()
    assert not hay_fallos(resultados), [r for r in resultados if r.estado == "FAIL"]

    # También cubre las reglas de negocio del brief sobre esta fixture:
    # el pedido 'test' (105) no tiene líneas, y el primer pedido de cada
    # usuario (order_number=1) es el único con days_since_prior_order NULL.
    por_id = {r.id: r for r in resultados}
    assert por_id["test_sin_lineas"].estado == "PASS"
    assert por_id["dspo_null_ssi_orden1"].estado == "PASS"
    assert por_id["dspo_no_null_si_no_orden1"].estado == "PASS"
    # El producto 6 (aisle 'missing') y el 7 (department 'missing') deben
    # aparecer en el reporte de calidad.
    assert por_id["productos_afectados_missing_other"].detalle == "productos_afectados=2"


def test_validador_detecta_reordered_corrupto(db_fixture):
    """Un FAIL real: se corrompe un valor y el validador debe reportarlo, no maquillarlo."""
    con = duckdb.connect(str(db_fixture))
    try:
        con.execute(
            "UPDATE order_products__prior SET reordered = 2 "
            "WHERE order_id = 101 AND product_id = 1"
        )
        resultados = check_rangos(con)
    finally:
        con.close()

    por_id = {r.id: r for r in resultados}
    assert por_id["reordered_rango"].estado == "FAIL"
    assert por_id["reordered_rango"].detalle == "filas_fuera_de_rango=1"
    assert hay_fallos(resultados)
