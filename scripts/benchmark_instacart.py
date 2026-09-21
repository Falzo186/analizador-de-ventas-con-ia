"""Benchmark de las consultas analíticas base sobre data/instacart.duckdb.

Para cada consulta (B1..B4) mide:
  - 1 corrida en FRÍO: proceso Python nuevo (subprocess), para que el caché
    de planes/buffers de DuckDB dentro del proceso no esté calentado. Ojo:
    esto NO limpia el caché de página del sistema operativo (requeriría root
    para `echo 3 > /proc/sys/vm/drop_caches`), así que "frío" aquí significa
    "proceso nuevo", no "disco realmente frío". Se documenta esta limitación
    en el reporte.
  - 3 corridas en CALIENTE: mismo proceso y misma conexión DuckDB, reutilizada.

Memoria: pico de RSS del proceso que corrió la consulta (vía `/usr/bin/time -v`
para la corrida fría, vía `/proc/self/status:VmHWM` para las calientes).
También reporta RAM libre (`MemAvailable` de `/proc/meminfo`) antes y después
de todo el benchmark, y si Ollama tiene algún modelo cargado en memoria
(`GET http://localhost:11434/api/ps`).

CLI:
    python -m scripts.benchmark_instacart --db data/instacart.duckdb
    python -m scripts.benchmark_instacart --single-query b1 --db data/instacart.duckdb   # uso interno (corrida fría)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import duckdb

MEMORY_LIMIT = "4GB"
UMBRAL_OBJETIVO_S = 5.0

QUERIES: dict[str, tuple[str, str]] = {
    "b1_recompra_por_producto": (
        "Tasa de recompra por producto (N1)",
        """
        SELECT product_id,
               SUM(CASE WHEN reordered = 1 THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS tasa_recompra,
               COUNT(*) AS n_lineas
        FROM order_products
        GROUP BY product_id
        """,
    ),
    "b2_pedidos_dia_hora": (
        "Pedidos por día x hora (N3)",
        """
        SELECT order_dow, order_hour_of_day, COUNT(*) AS n_pedidos
        FROM orders
        GROUP BY order_dow, order_hour_of_day
        ORDER BY order_dow, order_hour_of_day
        """,
    ),
    "b3_pares_top1000": (
        "Pares co-ocurrentes con soporte, top 1000 productos más frecuentes (N2)",
        """
        WITH top_productos AS (
            SELECT product_id
            FROM order_products
            GROUP BY product_id
            ORDER BY COUNT(*) DESC
            LIMIT 1000
        ),
        filtrado AS (
            SELECT order_id, product_id
            FROM order_products
            WHERE product_id IN (SELECT product_id FROM top_productos)
        ),
        total_pedidos AS (
            SELECT COUNT(DISTINCT order_id) AS n FROM order_products
        )
        SELECT a.product_id AS producto_a, b.product_id AS producto_b,
               COUNT(*) AS co_ocurrencias,
               COUNT(*)::DOUBLE / (SELECT n FROM total_pedidos) AS soporte
        FROM filtrado a
        JOIN filtrado b ON a.order_id = b.order_id AND a.product_id < b.product_id
        GROUP BY a.product_id, b.product_id
        ORDER BY co_ocurrencias DESC
        LIMIT 100
        """,
    ),
    "b4_variedad_por_usuario": (
        "Variedad por usuario: productos distintos y líneas totales (N8)",
        """
        SELECT o.user_id,
               COUNT(*) AS lineas_totales,
               COUNT(DISTINCT op.product_id) AS productos_distintos
        FROM order_products op
        JOIN orders o ON o.order_id = op.order_id
        GROUP BY o.user_id
        """,
    ),
}


def mem_available_mb() -> float | None:
    try:
        with open("/proc/meminfo") as fh:
            for linea in fh:
                if linea.startswith("MemAvailable:"):
                    kb = int(linea.split()[1])
                    return kb / 1024
    except OSError:
        return None
    return None


def vmhwm_mb() -> float | None:
    """Pico de RSS (VmHWM) del proceso actual, en MB."""
    try:
        with open("/proc/self/status") as fh:
            for linea in fh:
                if linea.startswith("VmHWM:"):
                    kb = int(linea.split()[1])
                    return kb / 1024
    except OSError:
        return None
    return None


def ollama_modelos_cargados() -> list[str] | None:
    """Modelos que Ollama tiene cargados en memoria ahora mismo, o None si no responde."""
    try:
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=2) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return [m.get("name") or m.get("model") for m in data.get("models", [])]


def _conectar(db_path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path, read_only=True)
    con.execute(f"SET memory_limit='{MEMORY_LIMIT}'")
    return con


def _ejecutar(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[float, int]:
    t0 = time.perf_counter()
    n = len(con.execute(sql).fetchall())
    return time.perf_counter() - t0, n


def _corrida_fria(nombre: str, db_path: str) -> dict:
    """Lanza este mismo script como subprocess con --single-query para medir
    una corrida en un proceso Python nuevo. Usa `/usr/bin/time -v` para
    capturar el pico de RSS del subproceso."""
    cmd = [
        "/usr/bin/time", "-v",
        sys.executable, "-m", "scripts.benchmark_instacart",
        "--single-query", nombre, "--db", db_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    if proc.returncode != 0:
        return {"error": proc.stderr[-2000:]}

    salida = json.loads(proc.stdout.strip().splitlines()[-1])
    m = re.search(r"Maximum resident set size \(kbytes\): (\d+)", proc.stderr)
    rss_mb = int(m.group(1)) / 1024 if m else None
    return {"elapsed_s": salida["elapsed_s"], "rows": salida["rows"], "rss_mb": rss_mb}


def _modo_single_query(nombre: str, db_path: str) -> int:
    """Modo interno: ejecuta UNA consulta en un proceso nuevo y emite JSON.
    Usado como subproceso por `_corrida_fria`; no se llama directamente."""
    _, sql = QUERIES[nombre]
    con = _conectar(db_path)
    elapsed, n = _ejecutar(con, sql)
    con.close()
    print(json.dumps({"elapsed_s": elapsed, "rows": n}))
    return 0


def benchmark_completo(db_path: str) -> list[dict]:
    resultados = []
    for nombre, (descripcion, sql) in QUERIES.items():
        print(f"\n=== {nombre}: {descripcion} ===")

        fria = _corrida_fria(nombre, db_path)
        if "error" in fria:
            print(f"  [FRÍO] ERROR: {fria['error']}")
            fria_ok = None
        else:
            fria_ok = fria
            print(f"  [FRÍO] {fria['elapsed_s']:.3f} s, {fria['rows']} filas, "
                  f"pico RSS {fria['rss_mb']:.1f} MB" if fria['rss_mb'] else
                  f"  [FRÍO] {fria['elapsed_s']:.3f} s, {fria['rows']} filas")

        con = _conectar(db_path)
        calientes = []
        for i in range(3):
            elapsed, n = _ejecutar(con, sql)
            rss = vmhwm_mb()
            calientes.append({"elapsed_s": elapsed, "rows": n, "rss_mb": rss})
            print(f"  [CALIENTE {i+1}/3] {elapsed:.3f} s, {n} filas, "
                  f"pico RSS acumulado {rss:.1f} MB" if rss else
                  f"  [CALIENTE {i+1}/3] {elapsed:.3f} s, {n} filas")
        con.close()

        min_caliente = min(c["elapsed_s"] for c in calientes)
        cumple = min_caliente < UMBRAL_OBJETIVO_S
        print(f"  Mejor tiempo en caliente: {min_caliente:.3f} s "
              f"({'CUMPLE' if cumple else 'NO CUMPLE'} objetivo <{UMBRAL_OBJETIVO_S:.0f}s)")

        resultados.append({
            "nombre": nombre,
            "descripcion": descripcion,
            "frio": fria_ok,
            "calientes": calientes,
            "min_caliente_s": min_caliente,
            "cumple_objetivo": cumple,
        })
    return resultados


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.benchmark_instacart")
    parser.add_argument("--db", default="data/instacart.duckdb")
    parser.add_argument("--single-query", default=None,
                        help=argparse.SUPPRESS)  # uso interno para la corrida fría
    args = parser.parse_args(argv)

    if args.single_query:
        return _modo_single_query(args.single_query, args.db)

    if not Path(args.db).exists():
        print(f"No existe {args.db}. Corre primero: python -m src.db.instacart")
        return 1

    ram_antes = mem_available_mb()
    modelos_antes = ollama_modelos_cargados()
    print(f"RAM disponible antes: {ram_antes:.0f} MB" if ram_antes else "RAM disponible antes: desconocida")
    print(f"Modelos de Ollama cargados antes: {modelos_antes if modelos_antes is not None else 'Ollama no accesible'}")
    print(f"memory_limit={MEMORY_LIMIT}  objetivo en caliente: <{UMBRAL_OBJETIVO_S:.0f} s")

    resultados = benchmark_completo(args.db)

    ram_despues = mem_available_mb()
    modelos_despues = ollama_modelos_cargados()
    print(f"\nRAM disponible después: {ram_despues:.0f} MB" if ram_despues else "\nRAM disponible después: desconocida")
    print(f"Modelos de Ollama cargados después: {modelos_despues if modelos_despues is not None else 'Ollama no accesible'}")

    fallos = [r for r in resultados if not r["cumple_objetivo"]]
    if fallos:
        print(f"\n{len(fallos)} de {len(resultados)} consultas NO cumplen el objetivo de <{UMBRAL_OBJETIVO_S:.0f}s en caliente.")
    else:
        print(f"\nLas {len(resultados)} consultas cumplen el objetivo de <{UMBRAL_OBJETIVO_S:.0f}s en caliente.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
