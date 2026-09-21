"""Genera docs/DATASET_INSTACART.md a partir de consultas reales.

Ninguna cifra de este documento se escribe a mano: todas salen de ejecutar
`src.db.instacart_validacion` contra `data/instacart.duckdb` (o la ruta que se
indique) y de leer la versión de DuckDB en uso. Si el archivo no existe,
avisa y no genera nada con cifras inventadas.

CLI:
    python -m scripts.generar_ficha_dataset [--db data/instacart.duckdb] \
        [--raw data/instacart/raw] [--out docs/DATASET_INSTACART.md]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.instacart_validacion import (  # noqa: E402
    CONTEOS_ESPERADOS_DATASET_COMPLETO,
    ORDERS_TEST_ESPERADO_DATASET_COMPLETO,
    ESTADO_FAIL,
    ESTADO_INFO,
    validar_completo,
)

FUENTE = (
    "The Instacart Online Grocery Shopping Dataset 2017, "
    "https://www.instacart.com/datasets/grocery-shopping-2017"
)
COPIA_USADA = "Kaggle, usuario yasserh"
LICENCIA = "Licencia original de Instacart: uso no comercial."

LIMITACIONES = [
    "`orders.eval_set` puede ser `prior`, `train` o `test`. Los pedidos "
    "`test` no tienen productos en ningún archivo: se excluyen de todo "
    "análisis de productos.",
    "Los productos de un pedido salen de la unión de `order_products__prior` "
    "y `order_products__train` (vista `order_products`).",
    "`days_since_prior_order` es NULL en el primer pedido de cada usuario y "
    "está topado en 30.",
    "`order_dow` (0-6) y `order_hour_of_day` (0-23) son anónimos: no se sabe "
    "a qué día real corresponde el 0.",
    "No hay precios, fechas reales ni datos demográficos: no se puede "
    "afirmar nada sobre ingresos o dinero con este dataset.",
]


def _tabla_dimensiones(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int, list[str]]]:
    filas = []
    for tabla in (
        "aisles", "departments", "products", "orders",
        "order_products__prior", "order_products__train",
    ):
        n = con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
        cols = [c[1] for c in con.execute(f"PRAGMA table_info('{tabla}')").fetchall()]
        filas.append((tabla, n, cols))
    return filas


def generar(db_path: Path, raw_dir: Path, out_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(
            f"No existe {db_path}. Corre primero: python -m src.db.instacart"
        )

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        dimensiones = _tabla_dimensiones(con)
        resultados = validar_completo(
            con, raw_dir,
            esperados=CONTEOS_ESPERADOS_DATASET_COMPLETO,
            orders_test_esperado=ORDERS_TEST_ESPERADO_DATASET_COMPLETO,
        )
        n_test = con.execute(
            "SELECT COUNT(*) FROM orders WHERE eval_set = 'test'"
        ).fetchone()[0]
    finally:
        con.close()

    n_pass = sum(1 for r in resultados if r.estado != ESTADO_INFO and r.estado != ESTADO_FAIL)
    n_fail = sum(1 for r in resultados if r.estado == ESTADO_FAIL)
    n_info = sum(1 for r in resultados if r.estado == ESTADO_INFO)

    L: list[str] = []
    L.append("# Ficha del dataset: Instacart Online Grocery Shopping Dataset 2017\n")
    L.append(f"_Generado automáticamente por `scripts/generar_ficha_dataset.py` el {date.today().isoformat()}. "
              "No editar a mano: cada cifra viene de una consulta contra "
              f"`{db_path}`._\n")

    L.append("## Fuente y licencia\n")
    L.append(f"- **Fuente original:** {FUENTE}")
    L.append(f"- **Copia usada:** {COPIA_USADA}")
    L.append(f"- **Licencia:** {LICENCIA}\n")

    L.append("## Limitaciones del dataset\n")
    for lim in LIMITACIONES:
        L.append(f"- {lim}")
    L.append("")

    L.append("## Tablas: filas y columnas (medidas con `COUNT(*)` / `PRAGMA table_info`)\n")
    L.append("| Tabla | Filas | Columnas |")
    L.append("|---|---:|---|")
    for tabla, n, cols in dimensiones:
        L.append(f"| `{tabla}` | {n:,} | {', '.join(cols)} |")
    L.append("")
    L.append(f"`orders` con `eval_set = 'test'`: **{n_test:,}** (excluidos de todo análisis de productos).\n")

    L.append("## Motor y versión\n")
    L.append(f"- **DuckDB:** {duckdb.__version__}")
    L.append(f"- **Archivo:** `{db_path}` ({db_path.stat().st_size / (1024*1024):,.1f} MB)\n")

    L.append("## Resultado de la validación (`src.db.instacart_validacion`)\n")
    L.append(f"**{n_pass} PASS, {n_fail} FAIL, {n_info} INFO** — detalle completo en "
              "`docs/FASE1_REPORTE.md`.\n")
    L.append("| Check | Estado | Detalle |")
    L.append("|---|---|---|")
    for r in resultados:
        L.append(f"| `{r.id}` | {r.estado} | {r.detalle} |")
    L.append("")

    out_path.write_text("\n".join(L), encoding="utf-8")
    print(f"Escrito {out_path} ({n_pass} PASS, {n_fail} FAIL, {n_info} INFO)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.generar_ficha_dataset")
    parser.add_argument("--db", default="data/instacart.duckdb")
    parser.add_argument("--raw", default="data/instacart/raw")
    parser.add_argument("--out", default="docs/DATASET_INSTACART.md")
    args = parser.parse_args(argv)

    generar(Path(args.db), Path(args.raw), Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
