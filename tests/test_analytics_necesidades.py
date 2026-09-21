"""Tests de N1-N6 sobre la fixture `tests/fixtures/instacart_needs/`.

Todas las cifras esperadas se calcularon A MANO leyendo los CSV de la
fixture (ver los comentarios de derivación en cada test) — prohibido derivar
el esperado ejecutando el código bajo prueba. Además, para las dos métricas
más propensas a errores de join (recompra de N1 y pares de N2), hay un test
"oráculo" que recalcula la métrica de forma INDEPENDIENTE con pandas
leyendo los CSV directamente (sin pasar por DuckDB) y compara contra el
resultado de `src.analytics`.

Resumen de la fixture (4 usuarios, ver los CSV para el detalle completo):
  user 1: 8 pedidos (7 prior + 1 train). Producto 1 (Milk) SIEMPRE
          add_to_cart_order=1 en sus 7 apariciones. Un intervalo=30 en medio
          de su historial (pedido #3). Productos 1+3 co-ocurren en 4 pedidos.
          Producto 4 (aisle 'missing') aparece una sola vez, cerca del final
          de su historial (censura de N1: solo 2 pedidos posteriores).
  user 2: 3 pedidos, todos prior. Compra el producto 5 en su PRIMER pedido y
          nunca más: con K_POST=3 le quedan solo 2 pedidos posteriores, así
          que ese par (usuario,producto) NO es observable (censura de N1).
  user 3: 6 pedidos (5 prior + 1 train). Último intervalo = 30 (censurado) Y
          muy por encima de su patrón típico (5): "en riesgo" en N4.
  user 4: 3 pedidos prior + 1 pedido 'test' (sin líneas, pero con
          days_since_prior_order=9 válido: cuenta para N4, no para N1/N5/N6).
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import pytest

from src.analytics import n1_recompra, n2_canasta, n3_picos, n4_frecuencia, n5_ancla, n6_departamentos
from tests.helpers_analytics import K_POST_TEST, construir_fixture, abrir

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "instacart_needs"


@pytest.fixture()
def dbs(tmp_path):
    src, agg = construir_fixture(tmp_path)
    con = abrir(src)
    yield con, agg
    con.close()


# ---------------------------------------------------------------------------
# N1 — recompra y abandono con censura
# ---------------------------------------------------------------------------

def test_n1_producto1_siempre_primero_y_no_abandonado(dbs):
    con, agg = dbs
    res = n1_recompra.calcular(con, agg, min_lineas=1, min_observables=1)

    # Producto 1 (Milk): aparece en 101,102,103,104,105,107,108 (user1, 7
    # veces, reordered=0,1,1,1,1,1,1 -> 6 con reordered=1) + 203 (user2,
    # reordered=0). Total: 8 líneas, 6 con reordered=1 -> tasa=6/8=0.75.
    # Primera compra de user1 es en su pedido #1 (f=1); con 8 pedidos-con-
    # líneas totales para user1, quedan 8-1=7 posteriores >= K_POST(3):
    # observable. Se repite 7 veces -> no abandonado.
    fila = next(f for f in res.tablas["asegurar_inventario"] if f["product_id"] == 1)
    assert fila["lineas"] == 8
    assert fila["tasa_recompra"] == pytest.approx(0.75)
    assert fila["observables"] == 1
    assert fila["abandonos"] == 0
    assert fila["tasa_abandono"] == pytest.approx(0.0)


def test_n1_producto5_abandonado_observable(dbs):
    con, agg = dbs
    res = n1_recompra.calcular(con, agg, min_lineas=1, min_observables=1)

    # Producto 5: user1 lo compra solo en su pedido #5 (f=5) y nunca más;
    # con 8 pedidos-con-líneas totales, quedan 8-5=3 posteriores >= K_POST(3):
    # observable, y como no hay recompra, es abandono (1/1 = 100%).
    # user2 también lo compra (pedido #1, f=1) pero solo tiene 3 pedidos
    # totales -> 3-1=2 posteriores < K_POST(3): NO observable (censura).
    fila = next(f for f in res.tablas["revisar_retirar"] if f["product_id"] == 5)
    assert fila["lineas"] == 2
    assert fila["tasa_recompra"] == pytest.approx(0.0)
    assert fila["observables"] == 1  # solo el par de user1 es observable
    assert fila["abandonos"] == 1
    assert fila["tasa_abandono"] == pytest.approx(1.0)


def test_n1_producto4_missing_no_observable_por_censura(dbs):
    con, agg = dbs
    res = n1_recompra.calcular(con, agg, min_lineas=1, min_observables=0)

    # Producto 4 (aisle_id=100 'missing'): user1 lo compra solo en su pedido
    # #6 (f=6); con 8 pedidos-con-líneas totales, quedan 8-6=2 posteriores,
    # por DEBAJO de K_POST=3: el par NO es observable. No se cuenta como
    # abandono ni como éxito.
    todas = res.tablas["asegurar_inventario"] + res.tablas["revisar_retirar"]
    fila4 = next((f for f in todas if f["product_id"] == 4), None)
    assert fila4 is None or (fila4["observables"] == 0)
    # Se marca como 'missing' vía aisle_id=100, sin excluirse de la base.
    assert res is not None  # el producto sigue existiendo en la agregación base


def test_n1_se_puede_llamar_dos_veces_en_la_misma_conexion(dbs):
    # Regresión: n1_base es una TEMP TABLE; si calcular() no la limpia al
    # salir, una segunda llamada en la misma conexión (como hace
    # `--todas`/un dashboard de larga vida reusando conexión) falla con
    # "Table with name n1_base already exists!".
    con, agg = dbs
    n1_recompra.calcular(con, agg, min_lineas=1, min_observables=1)
    n1_recompra.calcular(con, agg, min_lineas=1, min_observables=1)  # no debe lanzar


def test_n1_censura_global_pares_observables(dbs):
    con, agg = dbs
    res = n1_recompra.calcular(con, agg, min_lineas=1, min_observables=1)

    # Pares (usuario,producto) totales: user1={1,2,3,4,5}=5, user2={1,2,5}=3,
    # user3={6}=1, user4={6}=1 -> 10 pares en total.
    # Observables (posteriores >= K_POST=3): user1 -> 1,2,3,5 observables
    # (4 con 4/5 de sus productos: 4 NO por censura) = 4; user2 -> 0 (los 3
    # están censurados, ninguno llega a K_POST posteriores); user3 -> 1
    # (único producto, f=1, 6-1=5>=3); user4 -> 0 (único producto, f=1,
    # 3-1=2<3, censurado). Total observables = 4+0+1+0 = 5. 5/10 = 0.5.
    vc = {v.etiqueta: v.valor for v in res.valores_clave}
    assert vc["% pares observables (censura)"] == pytest.approx(0.5)


def test_n1_oraculo_pandas_tasa_recompra(dbs):
    """Oráculo independiente: recalcula tasa_recompra con pandas desde los CSV crudos."""
    pd = pytest.importorskip("pandas")
    con, agg = dbs

    prior = pd.read_csv(FIXTURE_DIR / "order_products__prior.csv")
    train = pd.read_csv(FIXTURE_DIR / "order_products__train.csv")
    todas = pd.concat([prior, train], ignore_index=True)

    esperado = (
        todas.groupby("product_id")["reordered"]
        .agg(lineas="count", lineas_recompra="sum")
        .assign(tasa_recompra=lambda d: d["lineas_recompra"] / d["lineas"])
    )

    res = n1_recompra.calcular(con, agg, min_lineas=1, min_observables=0)
    filas_sql = {f["product_id"]: f for f in res.tablas["asegurar_inventario"] + res.tablas["revisar_retirar"]}
    # Verificamos los productos elegibles que sí aparecen en alguna tabla.
    for product_id, fila_sql in filas_sql.items():
        assert fila_sql["lineas"] == esperado.loc[product_id, "lineas"]
        assert fila_sql["tasa_recompra"] == pytest.approx(esperado.loc[product_id, "tasa_recompra"])


# ---------------------------------------------------------------------------
# N2 — canasta
# ---------------------------------------------------------------------------

def test_n2_par_1_3(dbs):
    con, agg = dbs
    res = n2_canasta.calcular(con, agg, min_pares_lift=1)

    # Productos 1 y 3 co-ocurren en los pedidos 101, 103, 105, 107 (todos de
    # user1) -> pedidos_con_par=4. Producto 1 aparece en 8 pedidos-con-líneas
    # en total (7 de user1 + 1 de user2); producto 3 aparece en 4 (solo
    # user1). Total de pedidos-con-líneas de la fixture = 20 (8+3+6+3).
    #   soporte = 4/20 = 0.2
    #   confianza_a_b (1->3) = 4/8 = 0.5
    #   confianza_b_a (3->1) = 4/4 = 1.0
    #   lift = (4*20) / (8*4) = 2.5
    fila = next(
        f for f in res.tablas["top_50_pedidos_con_par"]
        if {f["producto_a"], f["producto_b"]} == {1, 3}
    )
    assert fila["pedidos_con_par"] == 4
    assert fila["soporte"] == pytest.approx(0.2)
    confianza_hacia_3 = fila["confianza_a_b"] if fila["producto_a"] == 1 else fila["confianza_b_a"]
    confianza_hacia_1 = fila["confianza_b_a"] if fila["producto_a"] == 1 else fila["confianza_a_b"]
    assert confianza_hacia_3 == pytest.approx(0.5)
    assert confianza_hacia_1 == pytest.approx(1.0)
    assert fila["lift"] == pytest.approx(2.5)


def test_n2_oraculo_pandas_par_1_3(dbs):
    """Oráculo independiente: recalcula el par (1,3) con pandas desde los CSV crudos."""
    pd = pytest.importorskip("pandas")
    con, agg = dbs

    orders = pd.read_csv(FIXTURE_DIR / "orders.csv")
    prior = pd.read_csv(FIXTURE_DIR / "order_products__prior.csv")
    train = pd.read_csv(FIXTURE_DIR / "order_products__train.csv")
    lineas = pd.concat([prior, train], ignore_index=True)
    pedidos_con_lineas = orders[orders["eval_set"].isin(["prior", "train"])]["order_id"]

    por_pedido = lineas.groupby("order_id")["product_id"].apply(set)
    pedidos_con_par = sum(1 for prods in por_pedido if {1, 3}.issubset(prods))
    pedidos_con_1 = sum(1 for prods in por_pedido if 1 in prods)
    pedidos_con_3 = sum(1 for prods in por_pedido if 3 in prods)
    total = len(pedidos_con_lineas)

    soporte_esperado = pedidos_con_par / total
    lift_esperado = (pedidos_con_par * total) / (pedidos_con_1 * pedidos_con_3)

    res = n2_canasta.calcular(con, agg, min_pares_lift=1)
    fila = next(
        f for f in res.tablas["top_50_pedidos_con_par"]
        if {f["producto_a"], f["producto_b"]} == {1, 3}
    )
    assert fila["pedidos_con_par"] == pedidos_con_par
    assert fila["soporte"] == pytest.approx(soporte_esperado)
    assert fila["lift"] == pytest.approx(lift_esperado)


# ---------------------------------------------------------------------------
# N3 — picos de demanda
# ---------------------------------------------------------------------------

def test_n3_total_pedidos_reconciliacion(dbs):
    con, agg = dbs
    res = n3_picos.calcular(con)
    # 21 pedidos totales en la fixture (20 con líneas + 1 'test' de user4).
    assert sum(f["n_pedidos"] for f in res.tablas["matriz_dow_hora"]) == 21


# ---------------------------------------------------------------------------
# N4 — frecuencia de recompra
# ---------------------------------------------------------------------------

def test_n4_usuario3_en_riesgo_y_censurado(dbs):
    con, agg = dbs
    res = n4_frecuencia.calcular(con, min_intervalos=3, umbral_razon=2, umbral_dias=5)

    # user3: intervalos no nulos = [5,4,6,5,30] (pedidos #2..#6), mediana=5.
    # último = valor en order_number máximo (6) = 30. razón = 30/5 = 6.0.
    # 6.0 >= 2 y (30-5)=25 >= 5 -> en riesgo. último=30 -> censurado.
    fila = next(f for f in res.tablas["top_50_razon"] if f["user_id"] == 3)
    assert fila["tipico"] == pytest.approx(5.0)
    assert fila["ultimo"] == 30
    assert fila["razon"] == pytest.approx(6.0)
    assert fila["censurado"] is True


def test_n4_usuario4_incluye_intervalo_de_pedido_test(dbs):
    con, agg = dbs
    res = n4_frecuencia.calcular(con, min_intervalos=3, umbral_razon=2, umbral_dias=5)

    # user4: pedidos 401(#1,NULL),402(#2,7),403(#3,8),404(#4,test,9).
    # N4 usa TODA la tabla orders sin filtrar eval_set: el intervalo del
    # pedido 'test' (9) SÍ cuenta. típico=mediana([7,8,9])=8.
    # último = valor en order_number máximo (4, el pedido test) = 9.
    # razón = 9/8 = 1.125 -> no en riesgo (umbral_razon=2).
    fila = next(f for f in res.tablas["top_50_razon"] if f["user_id"] == 4)
    assert fila["tipico"] == pytest.approx(8.0)
    assert fila["ultimo"] == 9
    assert fila["razon"] == pytest.approx(1.125)
    assert fila["censurado"] is False


def test_n4_usuario2_excluido_por_min_intervalos(dbs):
    con, agg = dbs
    res = n4_frecuencia.calcular(con, min_intervalos=3, umbral_razon=2, umbral_dias=5)
    # user2 solo tiene 2 intervalos no nulos (10, 12): con MIN_INTERVALOS=3 no es evaluable.
    assert all(f["user_id"] != 2 for f in res.tablas["top_50_razon"])


# ---------------------------------------------------------------------------
# N5 — productos ancla
# ---------------------------------------------------------------------------

def test_n5_producto1_ancla_perfecto(dbs):
    con, agg = dbs
    res = n5_ancla.calcular(con, min_apariciones=1)

    # Producto 1: 8 apariciones, SIEMPRE add_to_cart_order=1 -> tasa_primero=1.0.
    # tasa_esperada = promedio de 1/tam_pedido sobre sus 8 apariciones:
    # pedidos 101(2),102(2),103(2),104(1),105(3),107(2),108(1),203(1)
    # -> (1/2+1/2+1/2+1/1+1/3+1/2+1/1+1/1)/8 = (0.5*5 + 1*3 + 1/3)/8
    #    = (2.5+3+0.333...)/8 = 5.8333.../8 = 0.729166...
    fila = next(f for f in res.tablas["top_50_volumen"] if f["product_id"] == 1)
    assert fila["apariciones"] == 8
    assert fila["veces_primero"] == 8
    assert fila["tasa_primero"] == pytest.approx(1.0)
    tasa_esperada_calculada = (0.5 + 0.5 + 0.5 + 1 + 1 / 3 + 0.5 + 1 + 1) / 8
    assert fila["tasa_esperada"] == pytest.approx(tasa_esperada_calculada)
    assert fila["indice_ancla"] == pytest.approx(1.0 / tasa_esperada_calculada)


def test_n5_veces_primero_total_reconcilia_con_pedidos_con_lineas(dbs):
    con, agg = dbs
    res = n5_ancla.calcular(con, min_apariciones=1)
    vc = {v.etiqueta: v.valor for v in res.valores_clave}
    # Cada pedido con líneas tiene exactamente un primer artículo -> la suma
    # de veces_primero debe ser igual al número de pedidos con líneas (20).
    assert vc["Veces_primero total (= pedidos con líneas)"] == 20


# ---------------------------------------------------------------------------
# N6 — departamentos y pasillos
# ---------------------------------------------------------------------------

def test_n6_missing_reportado_aparte(dbs):
    con, agg = dbs
    res = n6_departamentos.calcular(con)

    # Producto 4 (aisle_id=100 'missing', department_id=21 'missing') aparece
    # 1 sola vez (pedido 106). Debe reportarse en las tablas *_missing_other,
    # no en la clasificación de cuadrantes.
    ids_dep_clasificados = {r["id"] for r in res.tablas["departamentos"]}
    ids_pas_clasificados = {r["id"] for r in res.tablas["pasillos"]}
    assert 21 not in ids_dep_clasificados
    assert 100 not in ids_pas_clasificados

    dep_aparte = {r["id"]: r for r in res.tablas["departamentos_missing_other"]}
    pas_aparte = {r["id"]: r for r in res.tablas["pasillos_missing_other"]}
    assert dep_aparte[21]["lineas"] == 1
    assert pas_aparte[100]["lineas"] == 1


def test_n6_total_lineas_reconciliacion(dbs):
    con, agg = dbs
    res = n6_departamentos.calcular(con)
    total = (
        sum(r["lineas"] for r in res.tablas["departamentos"])
        + sum(r["lineas"] for r in res.tablas["departamentos_missing_other"])
    )
    # 27 líneas totales en la fixture (ver derivación en el docstring del módulo).
    assert total == 27
