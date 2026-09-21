"""Validación del DuckDB de Instacart cargado por `src.db.instacart`.

Cada chequeo se reporta como PASS/FAIL (o INFO si es puramente descriptivo)
junto con la consulta SQL exacta usada para producirlo. Un FAIL se reporta
tal cual: este módulo no "ajusta" umbrales ni oculta un resultado inesperado.

CLI:
    python -m src.db.instacart_validacion [--db data/instacart.duckdb] [--raw data/instacart/raw]

Sale con código 0 si todos los PASS/FAIL pasaron, 1 si algo falló.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

ESTADO_PASS = "PASS"
ESTADO_FAIL = "FAIL"
ESTADO_INFO = "INFO"  # medición descriptiva; no cuenta como acierto ni fallo


@dataclass
class Chequeo:
    id: str
    descripcion: str
    query: str
    estado: str
    detalle: str


# Cifras del dataset completo, verificadas en docs/AUDITORIA.md contra los
# CSV originales completos (wc -l e inspección de eval_set). Se usan como
# `esperados` al validar data/instacart.duckdb; los tests con la fixture
# pequeña pasan sus propias cifras (calculadas a mano sobre la fixture).
CONTEOS_ESPERADOS_DATASET_COMPLETO: dict[str, int] = {
    "orders": 3_421_083,
    "order_products__prior": 32_434_489,
    "order_products__train": 1_384_617,
}
ORDERS_TEST_ESPERADO_DATASET_COMPLETO = 75_000


def _contar_lineas_csv(ruta: Path) -> int:
    """Filas de datos de un CSV (líneas totales - 1 cabecera).

    Deliberadamente NO usa duckdb ni pandas: es la verificación *independiente*
    que pide el punto 4a del prompt de esta fase, así no se compara el mismo
    camino de código contra sí mismo.
    """
    n = 0
    with open(ruta, "rb") as fh:
        for _ in fh:
            n += 1
    return n - 1


# ---------------------------------------------------------------------------
# (a) Conteos exactos
# ---------------------------------------------------------------------------

def check_conteos_exactos(
    con: duckdb.DuckDBPyConnection,
    raw_dir: Path,
    esperados: dict[str, int] | None = None,
    orders_test_esperado: int | None = None,
) -> list[Chequeo]:
    resultados: list[Chequeo] = []
    tablas_csv = {
        "orders": "orders.csv",
        "order_products__prior": "order_products__prior.csv",
        "order_products__train": "order_products__train.csv",
    }
    for tabla, csv_name in tablas_csv.items():
        ruta = raw_dir / csv_name
        n_csv = _contar_lineas_csv(ruta)
        n_duck = con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
        esperado = (esperados or {}).get(tabla)
        ok = n_csv == n_duck and (esperado is None or n_duck == esperado)
        detalle = f"wc(csv)={n_csv:,}  COUNT(*) duckdb={n_duck:,}"
        if esperado is not None:
            detalle += f"  esperado={esperado:,}"
        resultados.append(Chequeo(
            id=f"conteo_{tabla}",
            descripcion=f"Conteo exacto de {tabla} (wc -l independiente vs COUNT(*) vs esperado)",
            query=f"SELECT COUNT(*) FROM {tabla}",
            estado=ESTADO_PASS if ok else ESTADO_FAIL,
            detalle=detalle,
        ))

    n_test = con.execute("SELECT COUNT(*) FROM orders WHERE eval_set='test'").fetchone()[0]
    ok = orders_test_esperado is None or n_test == orders_test_esperado
    detalle = f"COUNT(*)={n_test:,}"
    if orders_test_esperado is not None:
        detalle += f"  esperado={orders_test_esperado:,}"
    resultados.append(Chequeo(
        id="conteo_orders_eval_set_test",
        descripcion="Pedidos con eval_set='test'",
        query="SELECT COUNT(*) FROM orders WHERE eval_set='test'",
        estado=ESTADO_PASS if ok else ESTADO_FAIL,
        detalle=detalle,
    ))
    return resultados


# ---------------------------------------------------------------------------
# (b) Dimensiones y distribución (descriptivo, sin asumir nada)
# ---------------------------------------------------------------------------

def reporte_dimensiones(con: duckdb.DuckDBPyConnection) -> list[Chequeo]:
    resultados: list[Chequeo] = []
    for tabla in ("aisles", "departments", "products"):
        n_filas = con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
        cols = con.execute(f"PRAGMA table_info('{tabla}')").fetchall()
        nombres_cols = ", ".join(c[1] for c in cols)
        resultados.append(Chequeo(
            id=f"dimensiones_{tabla}",
            descripcion=f"Filas y columnas de {tabla}",
            query=f"SELECT COUNT(*) FROM {tabla}; PRAGMA table_info('{tabla}')",
            estado=ESTADO_INFO,
            detalle=f"{n_filas:,} filas, {len(cols)} columnas ({nombres_cols})",
        ))

    dist = con.execute(
        "SELECT eval_set, COUNT(*) FROM orders GROUP BY eval_set ORDER BY eval_set"
    ).fetchall()
    resultados.append(Chequeo(
        id="distribucion_eval_set",
        descripcion="Distribución de orders.eval_set",
        query="SELECT eval_set, COUNT(*) FROM orders GROUP BY eval_set ORDER BY eval_set",
        estado=ESTADO_INFO,
        detalle=", ".join(f"{v}={n:,}" for v, n in dist),
    ))
    return resultados


# ---------------------------------------------------------------------------
# (c) Integridad: llaves únicas y huérfanos
# ---------------------------------------------------------------------------

def check_integridad(con: duckdb.DuckDBPyConnection) -> list[Chequeo]:
    resultados: list[Chequeo] = []

    for tabla, col in [
        ("orders", "order_id"),
        ("products", "product_id"),
        ("aisles", "aisle_id"),
        ("departments", "department_id"),
    ]:
        q = f"SELECT COUNT(*) - COUNT(DISTINCT {col}) FROM {tabla}"
        dup = con.execute(q).fetchone()[0]
        resultados.append(Chequeo(
            id=f"pk_unica_{tabla}",
            descripcion=f"{col} único en {tabla}",
            query=q,
            estado=ESTADO_PASS if dup == 0 else ESTADO_FAIL,
            detalle=f"duplicados={dup}",
        ))

    q = "SELECT COUNT(*) - COUNT(DISTINCT (order_id, product_id)) FROM order_products"
    dup = con.execute(q).fetchone()[0]
    resultados.append(Chequeo(
        id="pk_unica_order_products",
        descripcion="(order_id, product_id) único en la vista order_products",
        query=q,
        estado=ESTADO_PASS if dup == 0 else ESTADO_FAIL,
        detalle=f"duplicados={dup}",
    ))

    huerfanos = [
        ("products_sin_aisle",
         "SELECT COUNT(*) FROM products p LEFT JOIN aisles a ON a.aisle_id = p.aisle_id "
         "WHERE a.aisle_id IS NULL",
         "products -> aisles"),
        ("products_sin_department",
         "SELECT COUNT(*) FROM products p LEFT JOIN departments d ON d.department_id = p.department_id "
         "WHERE d.department_id IS NULL",
         "products -> departments"),
        ("order_products_sin_order",
         "SELECT COUNT(*) FROM order_products op LEFT JOIN orders o ON o.order_id = op.order_id "
         "WHERE o.order_id IS NULL",
         "order_products -> orders"),
        ("order_products_sin_product",
         "SELECT COUNT(*) FROM order_products op LEFT JOIN products p ON p.product_id = op.product_id "
         "WHERE p.product_id IS NULL",
         "order_products -> products"),
    ]
    for id_, q, desc in huerfanos:
        n = con.execute(q).fetchone()[0]
        resultados.append(Chequeo(
            id=id_,
            descripcion=f"Huérfanos {desc}",
            query=q,
            estado=ESTADO_PASS if n == 0 else ESTADO_FAIL,
            detalle=f"huerfanos={n}",
        ))
    return resultados


# ---------------------------------------------------------------------------
# (d) Rangos
# ---------------------------------------------------------------------------

def check_rangos(con: duckdb.DuckDBPyConnection) -> list[Chequeo]:
    resultados: list[Chequeo] = []
    checks = [
        ("reordered_rango",
         "SELECT COUNT(*) FROM order_products WHERE reordered NOT IN (0, 1)",
         "reordered ∈ {0,1}"),
        ("order_dow_rango",
         "SELECT COUNT(*) FROM orders WHERE order_dow NOT BETWEEN 0 AND 6",
         "order_dow entre 0 y 6"),
        ("order_hour_rango",
         "SELECT COUNT(*) FROM orders WHERE order_hour_of_day NOT BETWEEN 0 AND 23",
         "order_hour_of_day entre 0 y 23"),
        ("days_since_prior_rango",
         "SELECT COUNT(*) FROM orders WHERE days_since_prior_order IS NOT NULL "
         "AND days_since_prior_order NOT BETWEEN 0 AND 30",
         "days_since_prior_order entre 0 y 30 o NULL"),
    ]
    for id_, q, desc in checks:
        n = con.execute(q).fetchone()[0]
        resultados.append(Chequeo(
            id=id_, descripcion=desc, query=q,
            estado=ESTADO_PASS if n == 0 else ESTADO_FAIL,
            detalle=f"filas_fuera_de_rango={n}",
        ))

    q_contig = (
        "SELECT COUNT(*) FROM ("
        "  SELECT order_id, COUNT(*) AS n, MIN(add_to_cart_order) AS mn, "
        "         MAX(add_to_cart_order) AS mx, COUNT(DISTINCT add_to_cart_order) AS distintos "
        "  FROM order_products GROUP BY order_id "
        "  HAVING NOT (mn = 1 AND mx = n AND distintos = n)"
        ")"
    )
    n = con.execute(q_contig).fetchone()[0]
    resultados.append(Chequeo(
        id="add_to_cart_order_contiguo",
        descripcion="add_to_cart_order contiguo 1..n dentro de cada pedido",
        query=q_contig,
        estado=ESTADO_PASS if n == 0 else ESTADO_FAIL,
        detalle=f"pedidos_no_contiguos={n}",
    ))
    return resultados


# ---------------------------------------------------------------------------
# (e) Reglas de negocio del brief
# ---------------------------------------------------------------------------

def check_reglas_negocio(con: duckdb.DuckDBPyConnection) -> list[Chequeo]:
    resultados: list[Chequeo] = []
    checks = [
        ("dspo_null_ssi_orden1",
         "SELECT COUNT(*) FROM orders WHERE order_number = 1 AND days_since_prior_order IS NOT NULL",
         "days_since_prior_order es NULL cuando order_number=1"),
        ("dspo_no_null_si_no_orden1",
         "SELECT COUNT(*) FROM orders WHERE order_number != 1 AND days_since_prior_order IS NULL",
         "days_since_prior_order NO es NULL cuando order_number != 1"),
        ("test_sin_lineas",
         "SELECT COUNT(*) FROM orders o WHERE o.eval_set = 'test' "
         "AND o.order_id IN (SELECT order_id FROM order_products)",
         "pedidos 'test' no tienen líneas en order_products"),
        ("prior_train_con_al_menos_una_linea",
         "SELECT COUNT(*) FROM orders o WHERE o.eval_set IN ('prior', 'train') "
         "AND o.order_id NOT IN (SELECT order_id FROM order_products)",
         "todo pedido prior/train tiene al menos una línea"),
    ]
    for id_, q, desc in checks:
        n = con.execute(q).fetchone()[0]
        resultados.append(Chequeo(
            id=id_, descripcion=desc, query=q,
            estado=ESTADO_PASS if n == 0 else ESTADO_FAIL,
            detalle=f"excepciones={n}",
        ))
    return resultados


# ---------------------------------------------------------------------------
# (f) Calidad de datos (descriptivo)
# ---------------------------------------------------------------------------

def reporte_calidad(con: duckdb.DuckDBPyConnection) -> list[Chequeo]:
    resultados: list[Chequeo] = []

    aisles_raros = con.execute(
        "SELECT aisle_id, aisle FROM aisles WHERE lower(aisle) IN ('missing', 'other')"
    ).fetchall()
    deps_raros = con.execute(
        "SELECT department_id, department FROM departments WHERE lower(department) IN ('missing', 'other')"
    ).fetchall()
    resultados.append(Chequeo(
        id="aisles_missing_other",
        descripcion="Valores 'missing'/'other' en aisles",
        query="SELECT aisle_id, aisle FROM aisles WHERE lower(aisle) IN ('missing', 'other')",
        estado=ESTADO_INFO,
        detalle=str(aisles_raros) if aisles_raros else "ninguno",
    ))
    resultados.append(Chequeo(
        id="departments_missing_other",
        descripcion="Valores 'missing'/'other' en departments",
        query="SELECT department_id, department FROM departments WHERE lower(department) IN ('missing', 'other')",
        estado=ESTADO_INFO,
        detalle=str(deps_raros) if deps_raros else "ninguno",
    ))

    ids_aisles = [r[0] for r in aisles_raros]
    ids_deps = [r[0] for r in deps_raros]
    if ids_aisles or ids_deps:
        cond = []
        if ids_aisles:
            cond.append(f"aisle_id IN ({','.join(map(str, ids_aisles))})")
        if ids_deps:
            cond.append(f"department_id IN ({','.join(map(str, ids_deps))})")
        q = f"SELECT COUNT(*) FROM products WHERE {' OR '.join(cond)}"
        n = con.execute(q).fetchone()[0]
        resultados.append(Chequeo(
            id="productos_afectados_missing_other",
            descripcion="Productos con aisle_id/department_id 'missing' u 'other'",
            query=q,
            estado=ESTADO_INFO,
            detalle=f"productos_afectados={n}",
        ))

    tablas_columnas = [
        ("aisles", ["aisle_id", "aisle"]),
        ("departments", ["department_id", "department"]),
        ("products", ["product_id", "product_name", "aisle_id", "department_id"]),
        ("orders", ["order_id", "user_id", "eval_set", "order_number",
                     "order_dow", "order_hour_of_day", "days_since_prior_order"]),
        ("order_products__prior", ["order_id", "product_id", "add_to_cart_order", "reordered"]),
        ("order_products__train", ["order_id", "product_id", "add_to_cart_order", "reordered"]),
    ]
    for tabla, cols in tablas_columnas:
        partes = ", ".join(f"COUNT(*) FILTER (WHERE {c} IS NULL) AS {c}" for c in cols)
        q = f"SELECT {partes} FROM {tabla}"
        fila = con.execute(q).fetchone()
        nulos = {c: v for c, v in zip(cols, fila) if v}
        resultados.append(Chequeo(
            id=f"nulls_{tabla}",
            descripcion=f"NULLs por columna en {tabla}",
            query=q,
            estado=ESTADO_INFO,
            detalle=str(nulos) if nulos else "sin NULLs",
        ))
    return resultados


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def validar_completo(
    con: duckdb.DuckDBPyConnection,
    raw_dir: Path,
    esperados: dict[str, int] | None = None,
    orders_test_esperado: int | None = None,
) -> list[Chequeo]:
    resultados: list[Chequeo] = []
    resultados += check_conteos_exactos(con, raw_dir, esperados, orders_test_esperado)
    resultados += reporte_dimensiones(con)
    resultados += check_integridad(con)
    resultados += check_rangos(con)
    resultados += check_reglas_negocio(con)
    resultados += reporte_calidad(con)
    return resultados


def hay_fallos(resultados: list[Chequeo]) -> bool:
    return any(r.estado == ESTADO_FAIL for r in resultados)


def imprimir_resultados(resultados: list[Chequeo]) -> None:
    for r in resultados:
        print(f"[{r.estado:4}] {r.id:38} {r.detalle}")
        print(f"         SQL: {' '.join(r.query.split())}")
    n_pass = sum(1 for r in resultados if r.estado == ESTADO_PASS)
    n_fail = sum(1 for r in resultados if r.estado == ESTADO_FAIL)
    n_info = sum(1 for r in resultados if r.estado == ESTADO_INFO)
    print(f"\n{n_pass} PASS, {n_fail} FAIL, {n_info} INFO")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.db.instacart_validacion",
        description="Valida el DuckDB de Instacart: conteos, integridad, rangos, reglas y calidad.",
    )
    parser.add_argument("--db", default="data/instacart.duckdb")
    parser.add_argument("--raw", default="data/instacart/raw")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"No existe {db_path}. Corre primero: python -m src.db.instacart")
        return 1

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        resultados = validar_completo(
            con, Path(args.raw),
            esperados=CONTEOS_ESPERADOS_DATASET_COMPLETO,
            orders_test_esperado=ORDERS_TEST_ESPERADO_DATASET_COMPLETO,
        )
    finally:
        con.close()

    imprimir_resultados(resultados)
    return 1 if hay_fallos(resultados) else 0


if __name__ == "__main__":
    sys.exit(main())
