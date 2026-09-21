"""Tests de `src.analytics.agregados`: construcción, y detección de un
agregado "rancio" (corrupto/desactualizado) cuando la huella de la BD fuente
ya no coincide con la que tenía el agregado al construirse.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.analytics.agregados import AgregadoRancioError, abrir_agregado
from src.db.instacart import construir_duckdb
from tests.helpers_analytics import (
    K_POST_TEST,
    MIN_PARES_LIFT_TEST,
    MIN_SOPORTE_PRODUCTO_TEST,
    construir_fixture,
)

OTRA_FIXTURE_RAW = Path(__file__).parent / "fixtures" / "instacart_mini"


def test_agregado_valido_se_abre_sin_error(tmp_path):
    src, agg = construir_fixture(tmp_path)
    con_source = duckdb.connect(str(src), read_only=True)
    con_agg = abrir_agregado(agg, con_source)
    assert con_agg.execute("SELECT COUNT(*) FROM agg_pares").fetchone()[0] > 0
    assert con_agg.execute("SELECT COUNT(*) FROM agg_n1_producto").fetchone()[0] > 0
    con_agg.close()
    con_source.close()


def test_agregado_rancio_se_rechaza(tmp_path):
    """"Corrupción": la BD fuente cambia después de construir el agregado
    (aquí, se reconstruye con un CSV distinto) y la huella deja de coincidir.
    `abrir_agregado` debe rechazarlo con `AgregadoRancioError`, nunca servir
    datos desactualizados en silencio.
    """
    src, agg = construir_fixture(tmp_path)

    # "Corrompemos" la BD fuente: la reconstruimos con una fixture distinta
    # (la de Fase 1, con otro número de filas: 20 pedidos y 48 líneas en vez
    # de los 21/27 de instacart_needs) sin tocar el agregado ya construido
    # -- exactamente el escenario que agg_meta debe detectar.
    construir_duckdb(OTRA_FIXTURE_RAW, src)

    con_source = duckdb.connect(str(src), read_only=True)
    with pytest.raises(AgregadoRancioError):
        abrir_agregado(agg, con_source)
    con_source.close()


def test_agregado_inexistente_da_filenotfound(tmp_path):
    src, _ = construir_fixture(tmp_path)
    con_source = duckdb.connect(str(src), read_only=True)
    with pytest.raises(FileNotFoundError):
        abrir_agregado(tmp_path / "no_existe.duckdb", con_source)
    con_source.close()


def test_meta_guarda_parametros_usados(tmp_path):
    _, agg = construir_fixture(tmp_path)
    con_agg = duckdb.connect(str(agg), read_only=True)
    meta = dict(con_agg.execute("SELECT clave, valor FROM agg_meta").fetchall())
    assert meta["n1_k_post"] == str(K_POST_TEST)
    assert meta["n2_min_soporte_producto"] == str(MIN_SOPORTE_PRODUCTO_TEST)
    assert meta["n2_min_pares_lift"] == str(MIN_PARES_LIFT_TEST)
    con_agg.close()
