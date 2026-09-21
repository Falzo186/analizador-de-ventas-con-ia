"""N6 — Departamentos y pasillos: volumen vs. recompra.

Pregunta: ¿qué departamentos/pasillos son "fieles" (alta recompra) de alto
volumen o de nicho, y cuáles son "esporádicos"?
Decisión: dónde invertir espacio y publicidad.

Universo: líneas (order_products) ⨝ products ⨝ aisles/departments.

'missing' (aisle_id=100, department_id=21) y 'other' (aisle_id=6,
department_id=2) se reportan aparte y NO entran a la clasificación en
cuadrantes: no son categorías de producto reales, mezclarlas distorsionaría
la mediana de referencia.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import duckdb

from src.analytics.conexion import huella_bd, version_duckdb
from src.analytics.contrato import (
    Consulta,
    HuellaBD,
    Parametro,
    ResultadoNecesidad,
    Universo,
    ValorClave,
)

ID = "N6"
TITULO = "Departamentos y pasillos"
PREGUNTA = "¿Qué departamentos y pasillos son de alta recompra ('fieles') y cuáles esporádicos, cruzado con su volumen?"
DECISION = "Dónde invertir espacio en la app/tienda y publicidad."

AISLE_IDS_EXCLUIDOS = (100, 6)  # missing, other
DEPARTMENT_IDS_EXCLUIDOS = (21, 2)  # missing, other


def _clasificar(lineas: int, tasa: float, mediana_lineas: float, mediana_tasa: float) -> str:
    fiel = tasa >= mediana_tasa
    alto_volumen = lineas >= mediana_lineas
    if fiel and alto_volumen:
        return "fiel-alto-volumen"
    if fiel and not alto_volumen:
        return "fiel-nicho"
    if not fiel and alto_volumen:
        return "esporadico-alto-volumen"
    return "esporadico-nicho"


def calcular(con: duckdb.DuckDBPyConnection) -> ResultadoNecesidad:
    t_inicio = time.perf_counter()
    consultas: list[Consulta] = []

    sql_departamentos = """
    SELECT d.department_id, d.department, COUNT(*) AS lineas,
           SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS tasa_recompra
    FROM order_products op
    JOIN products p ON p.product_id = op.product_id
    JOIN departments d ON d.department_id = p.department_id
    GROUP BY d.department_id, d.department
    """
    filas_dep = con.execute(sql_departamentos).fetchall()
    consultas.append(Consulta("por_departamento", sql_departamentos.strip()))

    sql_pasillos = """
    SELECT a.aisle_id, a.aisle, COUNT(*) AS lineas,
           SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS tasa_recompra
    FROM order_products op
    JOIN products p ON p.product_id = op.product_id
    JOIN aisles a ON a.aisle_id = p.aisle_id
    GROUP BY a.aisle_id, a.aisle
    """
    filas_pas = con.execute(sql_pasillos).fetchall()
    consultas.append(Consulta("por_pasillo", sql_pasillos.strip()))

    sql_total = "SELECT COUNT(*) FROM order_products"
    lineas_totales = con.execute(sql_total).fetchone()[0]
    consultas.append(Consulta("lineas_totales", sql_total))

    def _procesar(filas, ids_excluidos):
        incluidas = [(id_, nombre, lin, tasa) for id_, nombre, lin, tasa in filas if id_ not in ids_excluidos]
        excluidas = [(id_, nombre, lin, tasa) for id_, nombre, lin, tasa in filas if id_ in ids_excluidos]
        lineas_ordenadas = sorted(l for _, _, l, _ in incluidas)
        tasas_ordenadas = sorted(t for _, _, _, t in incluidas)
        n = len(incluidas)

        def _mediana(vals: list[float]) -> float:
            m = len(vals)
            return vals[m // 2] if m % 2 else (vals[m // 2 - 1] + vals[m // 2]) / 2

        mediana_lineas = _mediana(lineas_ordenadas)
        mediana_tasa = _mediana(tasas_ordenadas)

        clasificadas = [
            {
                "id": id_, "nombre": nombre, "lineas": lin,
                "pct_lineas": lin / lineas_totales,
                "tasa_recompra": tasa,
                "cuadrante": _clasificar(lin, tasa, mediana_lineas, mediana_tasa),
            }
            for id_, nombre, lin, tasa in incluidas
        ]
        clasificadas.sort(key=lambda r: (-r["lineas"], r["id"]))

        reportadas_aparte = [
            {"id": id_, "nombre": nombre, "lineas": lin, "pct_lineas": lin / lineas_totales, "tasa_recompra": tasa}
            for id_, nombre, lin, tasa in sorted(excluidas, key=lambda x: x[0])
        ]
        return clasificadas, reportadas_aparte, mediana_lineas, mediana_tasa

    departamentos, dep_aparte, mediana_lineas_dep, mediana_tasa_dep = _procesar(filas_dep, DEPARTMENT_IDS_EXCLUIDOS)
    pasillos, pas_aparte, mediana_lineas_pas, mediana_tasa_pas = _procesar(filas_pas, AISLE_IDS_EXCLUIDOS)

    lineas_dep_aparte = sum(r["lineas"] for r in dep_aparte)
    lineas_pas_aparte = sum(r["lineas"] for r in pas_aparte)

    resumen_cuadrantes_dep: dict[str, int] = {}
    for r in departamentos:
        resumen_cuadrantes_dep[r["cuadrante"]] = resumen_cuadrantes_dep.get(r["cuadrante"], 0) + 1

    top_dep = max(departamentos, key=lambda r: r["lineas"])
    top_pas = max(pasillos, key=lambda r: r["lineas"])

    hallazgos = [
        f"El departamento con más líneas es {top_dep['nombre']!r} ({top_dep['lineas']:,} líneas, "
        f"{top_dep['pct_lineas']:.1%} del total, tasa_recompra={top_dep['tasa_recompra']:.1%}, "
        f"cuadrante={top_dep['cuadrante']}).",
        f"El pasillo con más líneas es {top_pas['nombre']!r} ({top_pas['lineas']:,} líneas, "
        f"{top_pas['pct_lineas']:.1%} del total, tasa_recompra={top_pas['tasa_recompra']:.1%}, "
        f"cuadrante={top_pas['cuadrante']}).",
        f"De los {len(departamentos)} departamentos clasificados (mediana de líneas={mediana_lineas_dep:,.0f}, "
        f"mediana de tasa_recompra={mediana_tasa_dep:.1%}): "
        + ", ".join(f"{v} en '{k}'" for k, v in sorted(resumen_cuadrantes_dep.items())) + ".",
        f"'missing'/'other' representan {lineas_dep_aparte:,} líneas a nivel departamento "
        f"({lineas_dep_aparte/lineas_totales:.1%} del total) y {lineas_pas_aparte:,} líneas a nivel "
        f"pasillo ({lineas_pas_aparte/lineas_totales:.1%}): se reportan aparte, no entran a los cuadrantes.",
    ]

    limitaciones = [
        "'missing' (sin categorizar) y 'other' (categoría general) no son categorías de producto "
        "reales: se excluyen de la clasificación en cuadrantes (mediana de referencia) y se "
        "reportan como grupo aparte.",
        "La mediana de volumen y de tasa_recompra se calcula sobre las unidades incluidas "
        "(excluyendo missing/other), no sobre el catálogo completo.",
        "No hay precios: 'invertir espacio' se basa solo en volumen de líneas y tasa de recompra, "
        "no en margen ni ingreso.",
    ]

    huella = huella_bd(con)
    duracion = time.perf_counter() - t_inicio

    return ResultadoNecesidad(
        id=ID,
        titulo=TITULO,
        pregunta=PREGUNTA,
        decision=DECISION,
        universo=Universo(
            texto="Líneas de la vista order_products (prior+train) unidas a products, aisles y departments.",
            filtros=["order_products__prior UNION ALL order_products__train"],
        ),
        parametros=[
            Parametro("mediana como corte de cuadrante (departamentos)", mediana_lineas_dep,
                      "Corte de volumen: mediana de líneas entre los 19 departamentos incluidos "
                      "(excluye 'missing'/'other'); la mediana, no un umbral arbitrario, define "
                      "'alto volumen' relativo a las demás categorías del mismo dataset."),
            Parametro("mediana como corte de cuadrante (pasillos)", mediana_lineas_pas,
                      "Igual que el anterior, sobre los 132 pasillos incluidos."),
        ],
        consultas=consultas,
        tablas={
            "departamentos": departamentos,
            "pasillos": pasillos,
            "departamentos_missing_other": dep_aparte,
            "pasillos_missing_other": pas_aparte,
        },
        tablas_criterio_corte={
            "departamentos": "Los 19 departamentos incluidos (excluye 'missing'/'other'), orden líneas desc",
            "pasillos": "Los 132 pasillos incluidos (excluye 'missing'/'other'), orden líneas desc",
            "departamentos_missing_other": "Los 2 departamentos excluidos ('missing' id=21, 'other' id=2), orden id asc",
            "pasillos_missing_other": "Los 2 pasillos excluidos ('missing' id=100, 'other' id=6), orden id asc",
        },
        valores_clave=[
            ValorClave("Departamento top por líneas", top_dep["nombre"], "departamento", "por_departamento"),
            ValorClave("Pasillo top por líneas", top_pas["nombre"], "pasillo", "por_pasillo"),
            ValorClave("Líneas en departamentos missing/other", lineas_dep_aparte, "líneas", "por_departamento"),
            ValorClave("Líneas en pasillos missing/other", lineas_pas_aparte, "líneas", "por_pasillo"),
        ],
        hallazgos=hallazgos,
        limitaciones=limitaciones,
        duracion_s=duracion,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella),
    )
