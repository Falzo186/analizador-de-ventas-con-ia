"""Tests de N7-N11 sobre `tests/fixtures/instacart_needs_b/` (Fase 2B).

Todas las cifras esperadas se calcularon A MANO (derivación en los
comentarios) y luego se verificaron ejecutando el código UNA VEZ antes de
escribir las aserciones (no al revés): la fixture y su derivación completa
están documentadas aquí.

Resumen de la fixture (4 usuarios; ver los CSV para el detalle):
  user 1: pedido #1 (prior, productos 1,2,3, tamaño 3) y pedido #10 (train,
          productos 1[repite],4[nuevo], tamaño 2) -> pareado N7.
  user 2: un solo pedido con líneas (#1, prior, tamaño 1 -> excluido de N10
          por MIN_TAM) y un pedido #2 'test' sin líneas -> "menos de W" en N8
          y "usuario sin evaluar" en N11 (no tiene pedido 'train').
  user 3: compra el producto 6 dos veces, LEJOS en el pasado (order_number
          1 y 2, con el último prior en order_number 11 -> d=10 y d=9), y el
          producto 7 una sola vez, MUY RECIENTE (order_number 11 -> d=0).
          Con DECAY=1.0 el producto 6 (más frecuente) queda primero; con
          DECAY<=0.9 el producto 7 (más reciente) lo supera: la recencia le
          gana a la frecuencia. Su pedido 'train' (#12) contiene el producto
          6 (ya comprado antes: alcanzable) y el producto 8 (nunca antes
          comprado: inalcanzable, el "techo" de N11).
  user 4: producto 9 ("Always Last") aparece en dos pedidos de tamaños
          distintos (3 y 2), siempre en la última posición del carrito.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.analytics import n7_evolucion, n8_rutinarios, n9_tamano, n10_impulso, n11_prediccion
from tests.helpers_analytics import construir_fixture_b, abrir

FIXTURE_DIR_B = Path(__file__).parent / "fixtures" / "instacart_needs_b"


@pytest.fixture()
def dbs(tmp_path):
    src, agg = construir_fixture_b(tmp_path)
    con = abrir(src)
    yield con, agg
    con.close()


# ---------------------------------------------------------------------------
# N7 — pareado 1 vs 10
# ---------------------------------------------------------------------------

def test_n7_pareado_usuario1(dbs):
    con, agg = dbs
    res = n7_evolucion.calcular(con, agg, min_usuarios_k=1)

    # user1: pedido #1 tamaño=3 (productos 1,2,3, todos reordered=0,
    # departamentos {1,2}=2 distintos); pedido #10 tamaño=2 (producto 1
    # reordered=1, producto 4 reordered=0, departamentos {1,2}=2 distintos).
    #   diff_tamano = 2-3 = -1
    #   diff_tasa_recompra = (1/2) - (0/3) = 0.5
    #   diff_productos_nuevos = 1 - 3 = -2  (líneas reordered=0 por pedido)
    #   diff_departamentos_distintos = 2-2 = 0
    # Solo 1 usuario tiene ambos pedidos -> STDDEV_SAMP indefinido (None).
    fila = res.tablas["pareado_1_vs_10"][0]
    assert fila["n_usuarios"] == 1
    assert fila["diff_tamano_media"] == pytest.approx(-1.0)
    assert fila["diff_tamano_std"] is None
    assert fila["diff_tasa_recompra_media"] == pytest.approx(0.5)
    assert fila["diff_productos_nuevos_media"] == pytest.approx(-2.0)
    assert fila["diff_departamentos_distintos_media"] == pytest.approx(0.0)


def test_n7_reordered_en_primer_pedido_no_asumido(dbs):
    con, agg = dbs
    res = n7_evolucion.calcular(con, agg, min_usuarios_k=1)
    # order101 (k=1, user1): 3 líneas, las 3 con reordered=0 (primera compra).
    # order201 (k=1, user2): 1 línea, reordered=0. Total k=1: 4 líneas, 0 con reordered=1.
    assert any("0 de 4 líneas" in l or "0.0000%" in l for l in res.limitaciones)


# ---------------------------------------------------------------------------
# N8 — usuario con menos de W pedidos
# ---------------------------------------------------------------------------

def test_n8_usuario2_excluido_por_w(dbs):
    con, agg = dbs
    res = n8_rutinarios.calcular(con, agg, w=5, percentil_rutinario=0.25, percentil_explorador=0.75)
    vc = {v.etiqueta: v.valor for v in res.valores_clave}
    # user2 solo tiene 1 pedido con líneas (order201; order202 es 'test' sin
    # líneas): con W=5 no alcanza la ventana mínima.
    ids_clasificables = set()
    # (no exponemos user_id en la tabla de perfil, así que verificamos por conteo)
    assert vc["Usuarios excluidos por W"] >= 1


# ---------------------------------------------------------------------------
# N9 — invariante básico sobre la fixture
# ---------------------------------------------------------------------------

def test_n9_histograma_suma_pedidos_con_lineas(dbs):
    con, agg = dbs
    res = n9_tamano.calcular(con, agg, min_pedidos=1)
    # Pedidos con líneas en la fixture: 101,102,301,302,303,304,401,402,201 = 9
    # (202 es 'test' sin líneas y no cuenta).
    assert sum(f["pedidos"] for f in res.tablas["histograma_tamano"]) == 9


# ---------------------------------------------------------------------------
# N10 — producto siempre al final / pedido de tamaño 1 excluido
# ---------------------------------------------------------------------------

def test_n10_producto_siempre_al_final(dbs):
    con, agg = dbs
    res = n10_impulso.calcular(con, agg, min_apariciones=1, min_usuarios=1, percentil_tasa_baja=0.25)

    # Producto 9 ("Always Last"): aparece en order401 (ato=3 de tamaño 3,
    # pos_norm=(3-1)/(3-1)=1.0) y order402 (ato=2 de tamaño 2,
    # pos_norm=(2-1)/(2-1)=1.0). Las DOS apariciones caen en el último tramo
    # (pos_norm>=0.8) -> share_final = 2/2 = 1.0.
    fila = next(f for f in res.tablas["top_50_indice_final"] if f["product_id"] == 9)
    assert fila["apariciones"] == 2
    assert fila["apariciones_ultimo_tramo"] == 2
    assert fila["share_final"] == pytest.approx(1.0)


def test_n10_pedido_tamano_1_excluido(dbs):
    con, agg = dbs
    res = n10_impulso.calcular(con, agg, min_apariciones=1, min_usuarios=1)
    vc = {v.etiqueta: v.valor for v in res.valores_clave}
    # Pedidos con líneas y su tamaño: 101=3, 102=2, 201=1, 301=1, 302=1,
    # 303=1, 304=2, 401=3, 402=2 (202 es 'test', sin líneas, no cuenta).
    # Tamaño < MIN_TAM(2): 201, 301, 302, 303 -> 4 pedidos de 1 producto
    # cada uno -> 4 líneas excluidas.
    assert vc["Líneas excluidas (MIN_TAM)"] == 4


# ---------------------------------------------------------------------------
# N11 — techo alcanzable, DECAY cambia el ranking, usuario sin evaluar
# ---------------------------------------------------------------------------

def test_n11_techo_alcanzable(dbs):
    con, agg = dbs
    res = n11_prediccion.calcular(con, agg, k_seleccion=1)
    vc = {v.etiqueta: v.valor for v in res.valores_clave}
    # Líneas objetivo (train): user1 (productos 1,4) + user3 (productos 6,8) = 4.
    # Alcanzables (ya en el historial prior de ESE usuario): producto 1
    # (user1 lo compró en order101) y producto 6 (user3 lo compró en
    # order301/302). NO alcanzables: producto 4 (nunca antes de user1) y
    # producto 8 (nunca antes de user3). Techo = 2/4 = 0.5.
    assert vc["Techo alcanzable"] == pytest.approx(0.5)


def test_n11_decay_cambia_el_orden_del_topk(dbs):
    con, agg = dbs
    con_agg = abrir(agg)
    try:
        # user3: producto 6 comprado 2 veces lejos en el pasado (d=10,9 sobre
        # 11 posibles); producto 7 comprado 1 vez muy reciente (d=0).
        #   score(6, DECAY=1.0) = 2/3 = 0.667 > score(7,1.0) = 1/3 = 0.333
        #   score(7, DECAY=0.9) = 0.576 > score(6,0.9) = 0.424 (ver derivación
        #   completa en el docstring del módulo: 0.9^10+0.9^9=0.736,
        #   denom=1.736, score(6)=0.736/1.736=0.424, score(7)=1/1.736=0.576).
        top_freq = n11_prediccion.predecir_usuario(con_agg, user_id=3, k=1, decay=1.0)
        top_recencia = n11_prediccion.predecir_usuario(con_agg, user_id=3, k=1, decay=0.9)
        assert top_freq[0]["product_id"] == 6
        assert top_recencia[0]["product_id"] == 7
        assert top_freq[0]["product_id"] != top_recencia[0]["product_id"]
    finally:
        con_agg.close()


def test_n11_usuario_test_no_se_evalua(dbs):
    con, agg = dbs
    con_agg = abrir(agg)
    try:
        # user2 no tiene pedido 'train' (su último pedido es 'test'): no
        # debe aparecer en agg_n11_usuarios (el universo evaluado de N11).
        fila = con_agg.execute(
            "SELECT COUNT(*) FROM agg_n11_usuarios WHERE user_id = 2"
        ).fetchone()[0]
        assert fila == 0
    finally:
        con_agg.close()

    res = n11_prediccion.calcular(con, agg, k_seleccion=1)
    vc = {v.etiqueta: v.valor for v in res.valores_clave}
    # Solo user1 y user3 tienen pedido 'train' en la fixture -> universo=2.
    assert vc["Usuarios evaluados"] == 2
