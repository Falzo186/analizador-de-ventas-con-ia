"""Tests de contrato: cada ResultadoNecesidad debe cumplir el contrato común
de `src.analytics.contrato` (JSON válido, sin NaN/Infinity, cada valor_clave
referencia una consulta real, cada tabla <= 500 filas).
"""

from __future__ import annotations

import json
import math

import pytest

from src.analytics.registro import NECESIDADES
from tests.helpers_analytics import construir_fixture, abrir


@pytest.fixture(scope="module")
def resultados(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("contrato")
    src, agg = construir_fixture(tmp_path)
    con = abrir(src)
    out = {}
    for nid, fn in NECESIDADES.items():
        if nid in ("N1", "N2"):
            out[nid] = fn(con, agg)
        else:
            out[nid] = fn(con)
    con.close()
    return out


def _sin_nan_inf(obj) -> bool:
    if isinstance(obj, float):
        return not (math.isnan(obj) or math.isinf(obj))
    if isinstance(obj, dict):
        return all(_sin_nan_inf(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return all(_sin_nan_inf(v) for v in obj)
    return True


@pytest.mark.parametrize("nid", sorted(NECESIDADES))
def test_json_valido_y_sin_nan(resultados, nid):
    res = resultados[nid]
    texto = res.a_json()
    data = json.loads(texto)  # debe parsear sin error
    assert "NaN" not in texto and "Infinity" not in texto
    assert _sin_nan_inf(data)


@pytest.mark.parametrize("nid", sorted(NECESIDADES))
def test_valores_clave_referencian_consulta_real(resultados, nid):
    res = resultados[nid]
    nombres_consulta = {c.nombre for c in res.consultas}
    for vc in res.valores_clave:
        assert vc.consulta_origen in nombres_consulta, (
            f"{nid}: valor_clave '{vc.etiqueta}' referencia '{vc.consulta_origen}' "
            f"que no está en consultas={nombres_consulta}"
        )


@pytest.mark.parametrize("nid", sorted(NECESIDADES))
def test_tablas_no_exceden_500_filas(resultados, nid):
    res = resultados[nid]
    for nombre, filas in res.tablas.items():
        assert len(filas) <= 500, f"{nid}: tabla '{nombre}' tiene {len(filas)} filas"
        assert nombre in res.tablas_criterio_corte


@pytest.mark.parametrize("nid", sorted(NECESIDADES))
def test_campos_obligatorios_presentes(resultados, nid):
    res = resultados[nid]
    assert res.id == nid
    assert res.titulo and res.pregunta and res.decision
    assert res.universo.texto
    assert res.consultas, f"{nid}: no registró ninguna consulta"
    assert res.hallazgos, f"{nid}: no generó hallazgos"
    assert res.limitaciones, f"{nid}: no declaró limitaciones"
    assert res.version_duckdb
    assert res.fecha_generacion
    assert res.huella_bd.order_products_total == (
        res.huella_bd.order_products_prior + res.huella_bd.order_products_train
    )


@pytest.mark.parametrize("nid", sorted(NECESIDADES))
def test_guardar_json_produce_archivo_leible(resultados, nid, tmp_path):
    res = resultados[nid]
    ruta = tmp_path / f"{nid}.json"
    res.guardar_json(ruta)
    data = json.loads(ruta.read_text(encoding="utf-8"))
    assert data["id"] == nid
