"""Fase 24: las 10 necesidades de una sola consulta (`src.analytics.necesidades`).

1. Oráculo en pandas sobre la fixture `tests/fixtures/instacart_needs/`: cada
   SQL de referencia se recalcula por un camino independiente (CSV ->
   pandas) y se compara fila por fila.
2. Integración contra `data/instacart.duckdb` (se salta si no existe): las
   cifras de la respuesta, medidas al construir esta fase
   (`docs/CLASIFICACION_FALLOS.md`, «Fase 24»).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.analytics import necesidades as nec
from src.analytics.conexion import abrir_conexion
from src.analytics.registro import NECESIDADES, invocar
from tests.helpers_analytics import FIXTURE_DIR, MIN_COMPRAS_TEST, abrir, construir_fixture

DB_REAL = Path("data/instacart.duckdb")


def test_son_10_necesidades_con_las_preguntas_pedidas():
    assert list(NECESIDADES) == [f"N{i}" for i in range(1, 11)]
    preguntas = [d.pregunta for d in nec.DEFINICIONES.values()]
    assert preguntas == [
        "¿Cuáles son los 10 productos más vendidos por número de unidades?",
        "¿Qué departamento tiene más pedidos en total?",
        "De los productos con al menos 50 compras, ¿cuáles tienen la tasa de recompra más alta?",
        "De los productos con al menos 50 compras, ¿cuáles tienen la tasa de recompra más baja?",
        "¿Cuál es el tamaño promedio del carrito (productos por pedido)?",
        "¿Cada cuántos días en promedio un cliente vuelve a comprar?",
        "¿Qué pasillo tiene más productos distintos a la venta?",
        "¿Qué pasillo tiene más unidades vendidas en total?",
        "¿Cuál es el producto que más veces se agrega primero al carrito (add_to_cart_order = 1)?",
        "¿Qué porcentaje de todas las líneas de pedido corresponde a productos recomprados (reordered = 1)?",
    ]


@pytest.mark.parametrize("nid", list(NECESIDADES))
def test_una_sola_consulta_y_es_la_de_la_definicion(nid, tmp_path):
    con = abrir(construir_fixture(tmp_path))
    r = invocar(nid, con)
    con.close()
    assert [c.nombre for c in r.consultas] == [nec.CONSULTA]
    assert r.consultas[0].sql == nec.DEFINICIONES[nid].sql()
    assert list(r.tablas) == [nec.CONSULTA]


# ---------------------------------------------------------------------------
# 1. Oráculo pandas sobre la fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def datos():
    leer = lambda n: pd.read_csv(FIXTURE_DIR / f"{n}.csv")  # noqa: E731
    lineas = pd.concat([leer("order_products__prior"), leer("order_products__train")], ignore_index=True)
    return {
        "lineas": lineas.merge(leer("products"), on="product_id"),
        "orders": leer("orders"), "products": leer("products"), "aisles": leer("aisles"),
        "departments": leer("departments"),
    }


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    c = abrir(construir_fixture(tmp_path_factory.mktemp("necesidades")))
    yield c
    c.close()


def _top(df: pd.DataFrame, col: str, *, asc: bool = False, n: int = 10, desempate: str) -> list[tuple]:
    df = df.sort_values([col, desempate], ascending=[asc, True]).head(n)
    return list(df.itertuples(index=False, name=None))


def test_n1_oraculo(con, datos):
    esperado = datos["lineas"].groupby("product_name").size().rename("unidades").reset_index()
    filas = invocar("N1", con).tablas["respuesta"]
    assert [(f["product_name"], f["unidades"]) for f in filas] == _top(esperado, "unidades", desempate="product_name")


def test_n2_oraculo_cuenta_pedidos_distintos(con, datos):
    lin = datos["lineas"].merge(datos["departments"], on="department_id")
    esperado = lin.groupby("department")["order_id"].nunique().rename("pedidos").reset_index()
    filas = invocar("N2", con).tablas["respuesta"]
    assert [(f["department"], f["pedidos"]) for f in filas] == _top(esperado, "pedidos", desempate="department")
    # Pedidos distintos, no líneas: en la fixture hay departamentos con más líneas que pedidos.
    assert (lin.groupby("department").size() > lin.groupby("department")["order_id"].nunique()).any()


@pytest.mark.parametrize("nid,asc", [("N3", False), ("N4", True)])
def test_n3_n4_oraculo_tasa_de_recompra(datos, nid, asc, tmp_path):
    g = datos["lineas"].groupby("product_name")["reordered"].agg(veces_comprado="size", recompras="sum")
    g = g[g["veces_comprado"] >= MIN_COMPRAS_TEST].reset_index()
    g["tasa_recompra"] = g["recompras"] / g["veces_comprado"]
    g = g.sort_values(["tasa_recompra", "veces_comprado", "product_name"], ascending=[asc, False, True]).head(10)
    con = abrir(construir_fixture(tmp_path))
    r = nec.calcular(nid, con, min_compras=MIN_COMPRAS_TEST)
    con.close()
    filas = r.tablas["respuesta"]
    assert filas, "la fixture debe tener productos con al menos MIN_COMPRAS_TEST compras"
    assert [f["product_name"] for f in filas] == list(g["product_name"])
    assert [f["tasa_recompra"] for f in filas] == pytest.approx(list(g["tasa_recompra"]))
    assert f"al menos {MIN_COMPRAS_TEST} compras" in r.hallazgos[0]


def test_n3_n4_con_50_compras_la_fixture_no_tiene_productos(con):
    for nid in ("N3", "N4"):
        r = invocar(nid, con)
        assert r.tablas["respuesta"] == [] and r.valores_clave == []
        assert "al menos 50 compras" in r.hallazgos[0]


def test_n5_oraculo(con, datos):
    por_pedido = datos["lineas"].groupby("order_id").size()
    f = invocar("N5", con).tablas["respuesta"][0]
    assert f["promedio_productos_por_pedido"] == pytest.approx(por_pedido.mean())
    assert (f["pedidos"], f["lineas"]) == (len(por_pedido), por_pedido.sum())


def test_n6_oraculo(con, datos):
    dias = datos["orders"]["days_since_prior_order"].dropna()
    f = invocar("N6", con).tablas["respuesta"][0]
    assert f["promedio_dias_entre_pedidos"] == pytest.approx(dias.mean())
    assert f["pedidos_con_intervalo"] == len(dias)


def test_n7_oraculo_cuenta_el_catalogo(con, datos):
    esperado = datos["products"].merge(datos["aisles"], on="aisle_id").groupby("aisle").size()
    esperado = esperado.rename("productos_distintos").reset_index()
    filas = invocar("N7", con).tablas["respuesta"]
    assert [(f["aisle"], f["productos_distintos"]) for f in filas] == _top(
        esperado, "productos_distintos", desempate="aisle")


def test_n8_oraculo(con, datos):
    esperado = datos["lineas"].merge(datos["aisles"], on="aisle_id").groupby("aisle").size()
    filas = invocar("N8", con).tablas["respuesta"]
    assert [(f["aisle"], f["unidades"]) for f in filas] == _top(
        esperado.rename("unidades").reset_index(), "unidades", desempate="aisle")


def test_n9_oraculo(con, datos):
    primeros = datos["lineas"][datos["lineas"]["add_to_cart_order"] == 1]
    esperado = primeros.groupby("product_name").size().rename("veces_primero").reset_index()
    filas = invocar("N9", con).tablas["respuesta"]
    assert [(f["product_name"], f["veces_primero"]) for f in filas] == _top(
        esperado, "veces_primero", desempate="product_name")


def test_n10_oraculo(con, datos):
    re = datos["lineas"]["reordered"]
    f = invocar("N10", con).tablas["respuesta"][0]
    assert f["pct_lineas_recompradas"] == pytest.approx(100 * re.mean())
    assert (f["lineas_recompradas"], f["lineas_totales"]) == (re.sum(), len(re))


# ---------------------------------------------------------------------------
# 2. Integración contra la BD real
# ---------------------------------------------------------------------------

#: nid -> (primer nombre, cifra) o cifra; medidos al construir la Fase 24.
ESPERADO_REAL = {
    "N1": ("Banana", 491_291),
    "N2": ("produce", 2_506_247),
    "N3": ("Raw Veggie Wrappers", 65 / 69),
    "N4": ("Vicks DayQuil and NyQuil Cold & Flu Relief Combo", 0.0),
    "N5": 33_819_106 / 3_346_083,
    "N6": 11.114836226863012,
    "N7": ("missing", 1_258),
    "N8": ("fresh fruits", 3_792_661),
    "N9": ("Banana", 115_521),
    "N10": 100 * 19_955_360 / 33_819_106,
}


@pytest.fixture(scope="module")
def con_real():
    if not DB_REAL.exists():
        pytest.skip(f"no existe {DB_REAL}")
    c = abrir_conexion(DB_REAL)
    yield c
    c.close()


@pytest.mark.integration
@pytest.mark.parametrize("nid", list(NECESIDADES))
def test_respuesta_contra_la_bd_real(con_real, nid):
    d = nec.DEFINICIONES[nid]
    fila = invocar(nid, con_real).tablas["respuesta"][0]
    esperado = ESPERADO_REAL[nid]
    if isinstance(esperado, tuple):
        assert (fila[d.columna_nombre], fila[d.columna_valor]) == (esperado[0], pytest.approx(esperado[1]))
    else:
        assert fila[d.columna_valor] == pytest.approx(esperado)
