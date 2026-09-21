"""Cargador de los CSV de Instacart a un archivo DuckDB.

Construye `data/instacart.duckdb` (o la ruta que se le indique) a partir de
los 6 CSV originales en `data/instacart/raw/`. El esquema es EXPLÍCITO: cada
columna se declara con su tipo entero compacto exacto (verificado contra los
rangos reales del dataset — ver `docs/AUDITORIA.md` y los comentarios de cada
tabla más abajo). No se usa autodetección de tipos de DuckDB en ningún punto:
`read_csv(..., auto_detect=False, columns={...})` fuerza el esquema que
nosotros decidimos, no el que DuckDB adivine.

CLI:
    python -m src.db.instacart --raw data/instacart/raw --out data/instacart.duckdb \
        [--rebuild] [--memory-limit 4GB] [--threads N]

Por qué construir en un archivo temporal y renombrar al final:
si el proceso muere a medias (falta de memoria, Ctrl+C, CSV corrupto), un
archivo `.duckdb` a medio cargar es peor que no tener ninguno: parece válido
pero le faltan tablas o filas. Se construye en `<out>.building-<pid>` y solo
se renombra a `<out>` (operación atómica en el mismo filesystem) si las 6
tablas se cargaron sin excepción.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import duckdb

# ---------------------------------------------------------------------------
# Esquema explícito
# ---------------------------------------------------------------------------
# Tipos elegidos verificando los rangos reales de cada columna contra los CSV
# (ver docs/AUDITORIA.md y docs/FASE1_REPORTE.md para el detalle de cómo se
# midió cada máximo):
#   aisle_id        1..134      -> UTINYINT  (0..255)
#   department_id   1..21       -> UTINYINT
#   product_id      1..49688    -> USMALLINT (0..65535)
#   order_id        1..3421083  -> UINTEGER  (0..4294967295)
#   user_id         1..206209   -> UINTEGER
#   order_number    1..100      -> UTINYINT
#   order_dow       0..6        -> UTINYINT
#   order_hour_of_day 0..23     -> UTINYINT
#   days_since_prior_order 0..30 o NULL -> UTINYINT (columna nullable)
#   add_to_cart_order 1..145    -> UTINYINT
#   reordered       0 o 1       -> UTINYINT (no BOOLEAN a propósito: así el
#                                  validador puede detectar un valor corrupto
#                                  como 2, que un BOOLEAN rechazaría al cargar
#                                  en vez de dejarlo para la validación)

_DDL = """
CREATE TABLE aisles (
    aisle_id    UTINYINT PRIMARY KEY,
    aisle       VARCHAR
);

CREATE TABLE departments (
    department_id UTINYINT PRIMARY KEY,
    department    VARCHAR
);

CREATE TABLE products (
    product_id    USMALLINT PRIMARY KEY,
    product_name  VARCHAR,
    aisle_id      UTINYINT,
    department_id UTINYINT
);

CREATE TABLE orders (
    order_id                UINTEGER PRIMARY KEY,
    user_id                 UINTEGER,
    eval_set                VARCHAR,
    order_number            UTINYINT,
    order_dow               UTINYINT,
    order_hour_of_day       UTINYINT,
    days_since_prior_order  UTINYINT
);

CREATE TABLE order_products__prior (
    order_id            UINTEGER,
    product_id          USMALLINT,
    add_to_cart_order   UTINYINT,
    reordered           UTINYINT
);

CREATE TABLE order_products__train (
    order_id            UINTEGER,
    product_id          USMALLINT,
    add_to_cart_order   UTINYINT,
    reordered           UTINYINT
);

-- order_products__test NO existe: los pedidos 'test' no tienen líneas de
-- producto en ningún archivo de origen (ver docs/BRIEF_INSTACART.md).
CREATE VIEW order_products AS
    SELECT order_id, product_id, add_to_cart_order, reordered
    FROM order_products__prior
    UNION ALL
    SELECT order_id, product_id, add_to_cart_order, reordered
    FROM order_products__train;
"""

# Nombre de archivo CSV -> (tabla destino, columnas EXACTAS en el orden real
# del archivo con su tipo DuckDB). El orden importa: al pasar `columns` a
# read_csv, DuckDB deja de autodetectar y usa este mapeo posicional tal cual.
_CARGAS: list[tuple[str, str, dict[str, str]]] = [
    ("aisles.csv", "aisles", {
        "aisle_id": "UTINYINT",
        "aisle": "VARCHAR",
    }),
    ("departments.csv", "departments", {
        "department_id": "UTINYINT",
        "department": "VARCHAR",
    }),
    ("products.csv", "products", {
        "product_id": "USMALLINT",
        "product_name": "VARCHAR",
        "aisle_id": "UTINYINT",
        "department_id": "UTINYINT",
    }),
    ("orders.csv", "orders", {
        "order_id": "UINTEGER",
        "user_id": "UINTEGER",
        "eval_set": "VARCHAR",
        "order_number": "UTINYINT",
        "order_dow": "UTINYINT",
        "order_hour_of_day": "UTINYINT",
        "days_since_prior_order": "UTINYINT",
    }),
    ("order_products__prior.csv", "order_products__prior", {
        "order_id": "UINTEGER",
        "product_id": "USMALLINT",
        "add_to_cart_order": "UTINYINT",
        "reordered": "UTINYINT",
    }),
    ("order_products__train.csv", "order_products__train", {
        "order_id": "UINTEGER",
        "product_id": "USMALLINT",
        "add_to_cart_order": "UTINYINT",
        "reordered": "UTINYINT",
    }),
]

DEFAULT_MEMORY_LIMIT = "4GB"  # deja RAM libre para Ollama en la misma máquina


def _columns_sql(columnas: dict[str, str]) -> str:
    partes = ", ".join(f"'{nombre}': '{tipo}'" for nombre, tipo in columnas.items())
    return "{" + partes + "}"


def construir_duckdb(
    raw_dir: Path,
    out_path: Path,
    *,
    memory_limit: str = DEFAULT_MEMORY_LIMIT,
    threads: int | None = None,
) -> None:
    """Crea `out_path` desde cero a partir de los CSV en `raw_dir`.

    Construye en un archivo temporal en el mismo directorio que `out_path` y
    solo renombra al final si todo salió bien (ver docstring del módulo).
    """
    for nombre_csv, _, _ in _CARGAS:
        if not (raw_dir / nombre_csv).exists():
            raise FileNotFoundError(f"Falta {raw_dir / nombre_csv}")

    tmp_path = out_path.parent / f"{out_path.name}.building-{os.getpid()}"
    if tmp_path.exists():
        tmp_path.unlink()

    inicio_total = time.perf_counter()
    con = duckdb.connect(str(tmp_path))
    try:
        con.execute(f"SET memory_limit='{memory_limit}'")
        if threads is not None:
            con.execute(f"SET threads={int(threads)}")

        print(f"memory_limit={memory_limit}  threads="
              f"{threads if threads is not None else '(default de DuckDB, todos los núcleos)'}")

        con.execute(_DDL)

        for i, (nombre_csv, tabla, columnas) in enumerate(_CARGAS, 1):
            ruta_csv = raw_dir / nombre_csv
            t0 = time.perf_counter()
            con.execute(
                f"""
                INSERT INTO {tabla}
                SELECT * FROM read_csv(
                    '{ruta_csv.as_posix()}',
                    columns = {_columns_sql(columnas)},
                    header = true,
                    delim = ',',
                    quote = '"',
                    escape = '"',
                    auto_detect = false
                )
                """
            )
            n = con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
            dt = time.perf_counter() - t0
            print(f"[{i}/{len(_CARGAS)}] {tabla:<24} {n:>12,} filas en {dt:6.2f} s")

        con.close()
    except Exception:
        con.close()
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    os.replace(tmp_path, out_path)
    total = time.perf_counter() - inicio_total
    tam_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\nListo: {out_path} ({tam_mb:,.1f} MB) en {total:.2f} s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.db.instacart",
        description="Carga los CSV de Instacart a un archivo DuckDB con esquema explícito.",
    )
    parser.add_argument("--raw", default="data/instacart/raw",
                        help="Carpeta con los 6 CSV originales de Instacart.")
    parser.add_argument("--out", default="data/instacart.duckdb",
                        help="Ruta del archivo DuckDB a crear.")
    parser.add_argument("--rebuild", action="store_true",
                        help="Si --out ya existe, lo reconstruye desde cero. "
                             "Sin esta bandera, si --out ya existe el comando no hace nada.")
    parser.add_argument("--memory-limit", default=DEFAULT_MEMORY_LIMIT,
                        help=f"Límite de memoria de DuckDB (default {DEFAULT_MEMORY_LIMIT}, "
                             "para dejar RAM disponible a Ollama en la misma máquina).")
    parser.add_argument("--threads", type=int, default=None,
                        help="Número de hilos de DuckDB (default: todos los núcleos disponibles).")
    args = parser.parse_args(argv)

    raw_dir = Path(args.raw)
    out_path = Path(args.out)

    if out_path.exists() and not args.rebuild:
        print(f"{out_path} ya existe. Usa --rebuild para reconstruirlo.")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and args.rebuild:
        out_path.unlink()

    construir_duckdb(
        raw_dir, out_path,
        memory_limit=args.memory_limit,
        threads=args.threads,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
