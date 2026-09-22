"""Utilidades compartidas por los tests de `src.analytics` (NO es un archivo de tests).

Construye la fixture `tests/fixtures/instacart_needs/` (una BD fuente chica +
su agregado) para que cada test no repita ese boilerplate.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from src.analytics.agregados import construir_agregados
from src.db.instacart import construir_duckdb

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "instacart_needs"

# Con 20 pedidos-con-líneas en la fixture, MIN_SOPORTE_PRODUCTO=0.05 implica
# min_pedidos_candidato=1: todo producto que aparezca en al menos 1 pedido
# es candidato (para poder probar N2 con la fixture chica, muy por debajo
# del 0.002 real). K_POST=3 y MIN_PARES_LIFT=1 igual: valores de prueba, no
# los defaults de producción.
K_POST_TEST = 3
MIN_SOPORTE_PRODUCTO_TEST = 0.05
MIN_PARES_LIFT_TEST = 1


def construir_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Construye la BD fuente y su agregado en `tmp_path`. Devuelve (src, agg)."""
    src = tmp_path / "needs.duckdb"
    agg = tmp_path / "needs_agg.duckdb"
    construir_duckdb(FIXTURE_DIR, src)
    construir_agregados(
        src, agg,
        min_soporte_producto=MIN_SOPORTE_PRODUCTO_TEST,
        min_pares_lift=MIN_PARES_LIFT_TEST,
        k_post=K_POST_TEST,
    )
    return src, agg


def abrir(db_path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=1")
    return con


# --- Fixture de Fase 2B (N7-N11): tests/fixtures/instacart_needs_b/ --------
FIXTURE_DIR_B = Path(__file__).parent / "fixtures" / "instacart_needs_b"


def construir_fixture_b(tmp_path: Path) -> tuple[Path, Path]:
    """Construye la BD fuente y su agregado de la fixture de Fase 2B."""
    src = tmp_path / "needs_b.duckdb"
    agg = tmp_path / "needs_b_agg.duckdb"
    construir_duckdb(FIXTURE_DIR_B, src)
    construir_agregados(
        src, agg,
        min_soporte_producto=MIN_SOPORTE_PRODUCTO_TEST,
        min_pares_lift=MIN_PARES_LIFT_TEST,
        k_post=K_POST_TEST,
    )
    return src, agg
