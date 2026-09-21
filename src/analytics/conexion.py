"""Conexión de solo lectura a DuckDB y huella de la base fuente.

Todo el módulo `src.analytics` abre la base de Instacart con
`read_only=True` y `memory_limit=4GB` (decisión ya tomada para esta fase,
ver docs/BRIEF_INSTACART.md): nunca escribe sobre `data/instacart.duckdb`,
y dosifica su uso de RAM para dejarle margen a Ollama en la misma máquina.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

MEMORY_LIMIT = "4GB"


def abrir_conexion(db_path: str | Path, *, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path), read_only=read_only)
    con.execute(f"SET memory_limit='{MEMORY_LIMIT}'")
    # threads=1: una consulta con AVG()/floats (p. ej. N5) puede sumar en distinto orden
    # entre corridas si DuckDB paraleliza la reducción, y la suma de floats no es asociativa
    # -- eso rompe el requisito de "mismo JSON byte a byte" en el último dígito. Se fuerza
    # ejecución determinista de un solo hilo; con el dataset de Instacart cada necesidad
    # sigue corriendo muy por debajo de 5s en caliente incluso así (medido en
    # docs/FASE2A_REPORTE.md).
    con.execute("SET threads=1")
    return con


def version_duckdb() -> str:
    return duckdb.__version__


def huella_bd(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Conteos que identifican de forma reproducible el contenido de la BD fuente.

    Se usa para (a) documentar la "huella de la BD" que pide el contrato de
    cada `ResultadoNecesidad`, y (b) para que `agregados.py` pueda detectar un
    agregado "rancio" si estos conteos ya no coinciden con los que tenía al
    construirse.
    """
    orders_total, orders_test = con.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE eval_set = 'test') FROM orders"
    ).fetchone()
    prior = con.execute("SELECT COUNT(*) FROM order_products__prior").fetchone()[0]
    train = con.execute("SELECT COUNT(*) FROM order_products__train").fetchone()[0]
    return {
        "orders_total": orders_total,
        "orders_test": orders_test,
        "order_products_prior": prior,
        "order_products_train": train,
    }
