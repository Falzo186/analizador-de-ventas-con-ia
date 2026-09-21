"""Registro de necesidades disponibles: id -> función `calcular`.

N1 y N2 necesitan la ruta al agregado (`data/instacart_agg.duckdb`); N3-N6
solo necesitan la conexión a la BD fuente. `invocar()` uniforma la llamada
para que `__main__.py` no tenga que saber la firma de cada una.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from src.analytics import n1_recompra, n2_canasta, n3_picos, n4_frecuencia, n5_ancla, n6_departamentos
from src.analytics.contrato import ResultadoNecesidad

NECESITA_AGREGADO = {"N1", "N2"}

NECESIDADES = {
    "N1": n1_recompra.calcular,
    "N2": n2_canasta.calcular,
    "N3": n3_picos.calcular,
    "N4": n4_frecuencia.calcular,
    "N5": n5_ancla.calcular,
    "N6": n6_departamentos.calcular,
}


def invocar(
    necesidad_id: str,
    con: duckdb.DuckDBPyConnection,
    agg_path: Path | None = None,
) -> ResultadoNecesidad:
    if necesidad_id not in NECESIDADES:
        raise ValueError(f"Necesidad desconocida: {necesidad_id}. Disponibles: {sorted(NECESIDADES)}")

    fn = NECESIDADES[necesidad_id]
    if necesidad_id in NECESITA_AGREGADO:
        if agg_path is None:
            raise ValueError(f"{necesidad_id} requiere --agg (ruta al agregado)")
        return fn(con, agg_path)
    return fn(con)
