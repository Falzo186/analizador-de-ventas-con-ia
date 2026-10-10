"""CLI de la capa analítica: calcula una o todas las necesidades y guarda su JSON.

    python -m src.analytics --necesidad N1 --db data/instacart.duckdb --json data/referencia/N1.json
    python -m src.analytics --todas --db data/instacart.duckdb --out-dir data/referencia
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.analytics.conexion import abrir_conexion
from src.analytics.registro import NECESIDADES, invocar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.analytics")
    parser.add_argument("--necesidad", choices=list(NECESIDADES), default=None)
    parser.add_argument("--todas", action="store_true")
    parser.add_argument("--db", default="data/instacart.duckdb")
    parser.add_argument("--json", default=None, help="Ruta de salida (solo con --necesidad)")
    parser.add_argument("--out-dir", default="data/referencia", help="Carpeta de salida (con --todas)")
    args = parser.parse_args(argv)

    if not args.necesidad and not args.todas:
        parser.error(f"Especifica --necesidad {'..'.join([next(iter(NECESIDADES)), list(NECESIDADES)[-1]])} o --todas")

    con = abrir_conexion(args.db)
    try:
        ids = [args.necesidad] if args.necesidad else list(NECESIDADES)
        for nid in ids:
            resultado = invocar(nid, con)
            destino = Path(args.json) if (args.json and args.necesidad) else Path(args.out_dir) / f"{nid}.json"
            resultado.guardar_json(destino)
            print(f"{nid}: {destino} ({resultado.duracion_s:.2f}s)")
    finally:
        con.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
