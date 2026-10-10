"""Fases 24 y 28: las 10 necesidades de una sola consulta (`src.analytics.necesidades`).
En la Fase 28 se reemplazaron N1, N2, N8, N9 y N10; N3-N7 no cambiaron.

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
        "¿Quiénes son los clientes con la frecuencia de compra más sostenida (más pedidos totales y menor "
        "promedio de días entre pedidos consecutivos)?",
        "¿Qué departamento tiene la tasa de recompra promedio más alta?",
        "De los productos con al menos 50 compras, ¿cuáles tienen la tasa de recompra más alta?",
        "De los productos con al menos 50 compras, ¿cuáles tienen la tasa de recompra más baja?",
        "¿Cuál es el tamaño promedio del carrito (productos por pedido)?",
        "¿Cada cuántos días en promedio un cliente vuelve a comprar?",
        "¿Qué pasillo tiene más productos distintos a la venta?",
        "¿En qué franja horaria del día se concentra el mayor volumen de pedidos?",
        "¿Cuáles son los dos productos que se compran juntos con más frecuencia (candidatos a combo/oferta)?",
        "¿Qué porcentaje de los pedidos son 100% de productos recomprados frente a los que son 100% productos "
        "nuevos?",
    ]


@pytest.mark.parametrize("nid", list(NECESIDADES))
def test_cada_sql_es_una_sola_sentencia_select(nid):
    """Fase 28: ninguna necesidad usa más de una consulta (ni WITH, que además rechaza la guarda del chat)."""
    from src.llm.safety_instacart import validar_sql

    sql = nec.DEFINICIONES[nid].sql()
    assert sql.lstrip().upper().startswith("SELECT") and ";" not in sql
    assert validar_sql(sql)


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


def test_n1_oraculo(datos, tmp_path):
    o = datos["orders"]
    g = o.groupby("user_id").agg(total_pedidos=("order_id", "size"),
                                 promedio_dias_entre_pedidos=("days_since_prior_order", "mean")).reset_index()
    g = g[g["total_pedidos"] >= 2].sort_values(["total_pedidos", "promedio_dias_entre_pedidos", "user_id"],
                                               ascending=[False, True, True]).head(10)
    con = abrir(construir_fixture(tmp_path))
    r = nec.calcular("N1", con, min_pedidos=2)  # la fixture no tiene clientes con 10 pedidos
    con.close()
    filas = r.tablas["respuesta"]
    assert filas, "la fixture debe tener clientes con al menos 2 pedidos"
    assert [(f["user_id"], f["total_pedidos"]) for f in filas] == list(zip(g["user_id"], g["total_pedidos"]))
    assert [f["promedio_dias_entre_pedidos"] for f in filas] == pytest.approx(list(g["promedio_dias_entre_pedidos"]))
    assert "al menos 2 pedidos" in r.hallazgos[0]


def test_n2_oraculo_tasa_de_recompra_por_departamento(con, datos):
    lin = datos["lineas"].merge(datos["departments"], on="department_id")
    g = lin.groupby("department")["reordered"].agg(veces_comprado="size", recompras="sum").reset_index()
    g["tasa_recompra"] = g["recompras"] / g["veces_comprado"]
    g = g.sort_values(["tasa_recompra", "department"], ascending=[False, True]).head(10)
    filas = invocar("N2", con).tablas["respuesta"]
    assert [f["department"] for f in filas] == list(g["department"])
    assert [f["tasa_recompra"] for f in filas] == pytest.approx(list(g["tasa_recompra"]))


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


def test_n8_oraculo_pedidos_por_hora(con, datos):
    g = datos["orders"].groupby("order_hour_of_day").size().rename("pedidos").reset_index()
    g = g.sort_values(["pedidos", "order_hour_of_day"], ascending=[False, True])
    filas = invocar("N8", con).tablas["respuesta"]
    assert [(f["order_hour_of_day"], f["pedidos"]) for f in filas] == list(g.itertuples(index=False, name=None))
    assert sum(f["pedidos"] for f in filas) == len(datos["orders"]), "todas las horas, todos los pedidos"


def test_n9_oraculo_pares_de_productos(datos, tmp_path):
    lin = datos["lineas"][["order_id", "product_id"]]
    minimo = 2
    frecuentes = lin.groupby("product_id").size()
    frecuentes = set(frecuentes[frecuentes >= minimo].index)
    pares = lin[lin["product_id"].isin(frecuentes)]
    pares = pares.merge(pares, on="order_id", suffixes=("_a", "_b"))
    pares = pares[pares["product_id_a"] < pares["product_id_b"]]
    g = pares.groupby(["product_id_a", "product_id_b"]).size().rename("pedidos_juntos").reset_index()
    nombres = datos["products"].set_index("product_id")["product_name"]
    g["producto_a"], g["producto_b"] = g["product_id_a"].map(nombres), g["product_id_b"].map(nombres)
    g = g.sort_values(["pedidos_juntos", "producto_a", "producto_b"], ascending=[False, True, True]).head(10)
    con = abrir(construir_fixture(tmp_path))
    r = nec.calcular("N9", con, min_pedidos_producto=minimo)  # el umbral real (10,000) deja la fixture vacía
    con.close()
    filas = r.tablas["respuesta"]
    assert filas, "la fixture debe tener pares de productos que se compran juntos"
    assert [(f["producto_a"], f["producto_b"], f["pedidos_juntos"]) for f in filas] == list(
        zip(g["producto_a"], g["producto_b"], g["pedidos_juntos"]))


def test_n10_oraculo_pedidos_todo_recomprado_y_todo_nuevo(con, datos):
    por_pedido = datos["lineas"].groupby("order_id")["reordered"].agg(["size", "sum"])
    todo_rec = int((por_pedido["sum"] == por_pedido["size"]).sum())
    todo_nuevo = int((por_pedido["sum"] == 0).sum())
    f = invocar("N10", con).tablas["respuesta"][0]
    assert (f["pedidos_todo_recomprado"], f["pedidos_todo_nuevo"], f["pedidos"]) == (todo_rec, todo_nuevo,
                                                                                      len(por_pedido))
    assert f["pct_pedidos_todo_recomprado"] == pytest.approx(100 * todo_rec / len(por_pedido))
    assert f["pct_pedidos_todo_nuevo"] == pytest.approx(100 * todo_nuevo / len(por_pedido))


# ---------------------------------------------------------------------------
# 2. Integración contra la BD real
# ---------------------------------------------------------------------------

#: nid -> (identificador de la primera fila, cifra), o cifras de la fila
#: (escalares). Medidos al construir las Fases 24 (N3-N7) y 28 (N1, N2, N8-N10).
ESPERADO_REAL = {
    "N1": ((46408,), 23 / 99),          # 100 pedidos; 0.2323 días en promedio
    "N2": (("dairy eggs",), 3_773_723 / 5_631_067),
    "N3": (("Raw Veggie Wrappers",), 65 / 69),
    "N4": (("Vicks DayQuil and NyQuil Cold & Flu Relief Combo",), 0.0),
    "N5": [33_819_106 / 3_346_083],
    "N6": [11.114836226863012],
    "N7": (("missing",), 1_258),
    "N8": ((10,), 288_418),
    "N9": (("Bag of Organic Bananas", "Organic Hass Avocado"), 64_761),
    "N10": [100 * 719_207 / 3_346_083, 100 * 397_115 / 3_346_083],
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
        nombres = tuple(fila[c] for c in (d.columna_nombre, *d.columnas_nombre_extra))
        assert (nombres, fila[d.columna_valor]) == (esperado[0], pytest.approx(esperado[1]))
    else:
        assert [fila[c] for c in (d.columna_valor, *d.columnas_valor_extra)] == pytest.approx(esperado)


@pytest.mark.integration
def test_n9_el_umbral_no_cambia_el_top_real(con_real):
    """Fase 28: el umbral de 10,000 pedidos por producto es exacto si el par número 10 tiene al menos esas
    co-ocurrencias (un par con K co-ocurrencias exige K pedidos de cada producto)."""
    filas = invocar("N9", con_real).tablas["respuesta"]
    assert len(filas) == 10 and filas[-1]["pedidos_juntos"] >= nec.MIN_PEDIDOS_PRODUCTO_PAR_DEFAULT
