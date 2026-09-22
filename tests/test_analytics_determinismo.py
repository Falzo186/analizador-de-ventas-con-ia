"""Determinismo: mismos datos + mismos parámetros => mismo JSON byte a byte
(descontando los campos volátiles `fecha_generacion` y `duracion_s`, que
cambian por diseño en cada corrida). Corre cada necesidad DOS veces sobre la
misma fixture y compara `huella_contenido` (Fase 2B, C4) además de
verificar que el resto del documento sea idéntico byte a byte.

Nota: esto mismo se verificó manualmente contra el dataset real completo
(33.8M líneas) en `docs/FASE2A_REPORTE.md` y `docs/FASE2B_REPORTE.md` --
aquí se automatiza sobre la fixture chica para que corra en cada
`pytest -q` sin depender de `data/instacart.duckdb`.
"""

from __future__ import annotations

import json

import pytest

from src.analytics.registro import NECESIDADES, NECESITA_AGREGADO
from tests.helpers_analytics import construir_fixture, abrir


def _sin_volatiles(resultado) -> dict:
    data = resultado.a_dict()
    data.pop("fecha_generacion", None)
    data.pop("duracion_s", None)
    return data


@pytest.mark.parametrize("nid", sorted(NECESIDADES))
def test_determinismo_dos_corridas(nid, tmp_path):
    src, agg = construir_fixture(tmp_path)
    fn = NECESIDADES[nid]

    con1 = abrir(src)
    r1 = fn(con1, agg) if nid in NECESITA_AGREGADO else fn(con1)
    con1.close()

    con2 = abrir(src)
    r2 = fn(con2, agg) if nid in NECESITA_AGREGADO else fn(con2)
    con2.close()

    d1, d2 = r1.a_dict(), r2.a_dict()

    # huella_contenido debe coincidir...
    assert d1["huella_contenido"] == d2["huella_contenido"], (
        f"{nid}: huella_contenido difiere entre dos corridas idénticas (no determinista)"
    )
    # ...y el resto del documento debe ser idéntico BYTE A BYTE (no solo el hash).
    texto1 = json.dumps(_sin_volatiles(r1), sort_keys=True, ensure_ascii=False)
    texto2 = json.dumps(_sin_volatiles(r2), sort_keys=True, ensure_ascii=False)
    assert texto1 == texto2, f"{nid}: el JSON difiere entre dos corridas idénticas (no determinista)"
