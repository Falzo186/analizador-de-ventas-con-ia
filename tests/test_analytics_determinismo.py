"""Determinismo: mismos datos + mismos parámetros => mismo JSON byte a byte
(descontando `fecha_generacion` y `duracion_s`, que cambian por diseño en
cada corrida). Corre cada necesidad DOS veces sobre la misma fixture y
compara el hash del resto del documento.

Nota: esto mismo se verificó manualmente contra el dataset real completo
(33.8M líneas) en `docs/FASE2A_REPORTE.md` -- aquí se automatiza sobre la
fixture chica para que corra en cada `pytest -q` sin depender de
`data/instacart.duckdb`.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from src.analytics.registro import NECESIDADES
from tests.helpers_analytics import construir_fixture, abrir


def _hash_sin_campos_variables(resultado) -> str:
    data = resultado.a_dict()
    data.pop("fecha_generacion", None)
    data.pop("duracion_s", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("nid", sorted(NECESIDADES))
def test_determinismo_dos_corridas(nid, tmp_path):
    src, agg = construir_fixture(tmp_path)
    fn = NECESIDADES[nid]

    con1 = abrir(src)
    r1 = fn(con1, agg) if nid in ("N1", "N2") else fn(con1)
    con1.close()

    con2 = abrir(src)
    r2 = fn(con2, agg) if nid in ("N1", "N2") else fn(con2)
    con2.close()

    assert _hash_sin_campos_variables(r1) == _hash_sin_campos_variables(r2), (
        f"{nid}: el JSON difiere entre dos corridas idénticas (no determinista)"
    )
