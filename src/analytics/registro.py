"""Registro de necesidades disponibles: id -> función `calcular(con)`.

Fase 24: las 10 necesidades son de una sola consulta cada una
(`src.analytics.necesidades`) y solo necesitan la conexión a la BD fuente;
ya no existe el agregado `data/instacart_agg.duckdb` de las Fases 2A/2B.
`invocar()` uniforma la llamada para el CLI y `src.dashboard.servicio`.
"""

from __future__ import annotations

from functools import partial

import duckdb

from src.analytics import necesidades
from src.analytics.contrato import ResultadoNecesidad
from src.analytics.necesidades import DEFINICIONES, DefNecesidad

NECESIDADES = {nid: partial(necesidades.calcular, nid) for nid in necesidades.IDS}


def definicion(necesidad_id: str) -> DefNecesidad:
    if necesidad_id not in DEFINICIONES:
        raise ValueError(f"Necesidad desconocida: {necesidad_id}. Disponibles: {list(DEFINICIONES)}")
    return DEFINICIONES[necesidad_id]


def invocar(necesidad_id: str, con: duckdb.DuckDBPyConnection) -> ResultadoNecesidad:
    definicion(necesidad_id)
    return NECESIDADES[necesidad_id](con)
