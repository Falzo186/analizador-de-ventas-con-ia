"""Utilidades compartidas por los tests de `src.analytics` (NO es un archivo de tests).

Construye la fixture `tests/fixtures/instacart_needs/` (una BD fuente chica)
para que cada test no repita ese boilerplate. Fase 24: ya no hay agregado
(`data/instacart_agg.duckdb`); las 10 necesidades solo leen la BD fuente.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from src.db.instacart import construir_duckdb

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "instacart_needs"

#: La fixture tiene 6 productos y 22 líneas: ninguno llega a 50 compras. Para
#: probar N3/N4 con datos se baja `min_compras` (parámetro de prueba, no el de
#: producción).
MIN_COMPRAS_TEST = 2


def construir_fixture(tmp_path: Path) -> Path:
    """Construye la BD fuente de la fixture en `tmp_path` y devuelve su ruta."""
    src = tmp_path / "needs.duckdb"
    construir_duckdb(FIXTURE_DIR, src)
    return src


def abrir(db_path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=1")
    return con
