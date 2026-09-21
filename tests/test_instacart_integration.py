"""Validación de integración contra el DuckDB real de Instacart.

Se salta automáticamente si `data/instacart.duckdb` no existe (no todos los
entornos que corren `pytest` tienen el dataset completo de 620 MB descargado
y cargado). Marcado @pytest.mark.integration; el marker está registrado en
`pyproject.toml`.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.db.instacart_validacion import (
    CONTEOS_ESPERADOS_DATASET_COMPLETO,
    ORDERS_TEST_ESPERADO_DATASET_COMPLETO,
    hay_fallos,
    validar_completo,
)

RAIZ = Path(__file__).resolve().parents[1]
DB_PATH = RAIZ / "data" / "instacart.duckdb"
RAW_DIR = RAIZ / "data" / "instacart" / "raw"

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not DB_PATH.exists(),
    reason=f"{DB_PATH} no existe; corre `python -m src.db.instacart` primero",
)
def test_validacion_completa_sobre_dataset_real():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        resultados = validar_completo(
            con, RAW_DIR,
            esperados=CONTEOS_ESPERADOS_DATASET_COMPLETO,
            orders_test_esperado=ORDERS_TEST_ESPERADO_DATASET_COMPLETO,
        )
    finally:
        con.close()

    fallos = [r for r in resultados if r.estado == "FAIL"]
    assert not hay_fallos(resultados), fallos
