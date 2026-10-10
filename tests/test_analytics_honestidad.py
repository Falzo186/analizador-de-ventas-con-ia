"""Honestidad: ningún hallazgo debe mencionar dinero/ingresos/precios (el
dataset no tiene esa información). En `limitaciones` sí pueden aparecer,
justamente para aclarar que no existen.
"""

from __future__ import annotations

import re

import pytest

from src.analytics.registro import NECESIDADES
from tests.helpers_analytics import construir_fixture, abrir

PATRON_PROHIBIDO = re.compile(r"ingres|dinero|ganancia|precio|revenue|\$|USD|MXN", re.IGNORECASE)


@pytest.fixture(scope="module")
def resultados(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("honestidad")
    src = construir_fixture(tmp_path)
    con = abrir(src)
    out = {}
    for nid, fn in NECESIDADES.items():
        out[nid] = fn(con)
    con.close()
    return out


@pytest.mark.parametrize("nid", sorted(NECESIDADES))
def test_hallazgos_sin_lenguaje_de_dinero(resultados, nid):
    res = resultados[nid]
    for h in res.hallazgos:
        assert not PATRON_PROHIBIDO.search(h), f"{nid}: hallazgo con lenguaje de dinero: {h!r}"


def test_al_menos_una_necesidad_aclara_ausencia_de_precios(resultados):
    # No es obligatorio en cada necesidad, pero el dataset no tiene precios y
    # varias limitaciones lo aclaran explícitamente (ahí sí pueden mencionar
    # la palabra, para explicar la ausencia).
    alguna = any(
        any("precio" in l.lower() or "ingreso" in l.lower() for l in res.limitaciones)
        for res in resultados.values()
    )
    assert alguna
