"""N1 — Recompra por producto, con abandono controlado por censura.

Pregunta: ¿qué productos aseguran inventario porque se recompran mucho, y
cuáles se prueban y se abandonan?
Decisión: qué asegurar en inventario y qué revisar o retirar.

Universo: líneas (vista `order_products`, prior+train) ⨝ `products`.

Esta necesidad usa el agregado precalculado `agg_n1_producto` (ver
`src.analytics.agregados`): el pipeline en vivo (agregado base + abandono con
control de censura) mide ~6s en caliente sobre el dataset completo, por
encima del objetivo de 5s del brief — la medición está en
`docs/FASE2A_REPORTE.md`.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.analytics.agregados import abrir_agregado
from src.analytics.conexion import huella_bd, version_duckdb
from src.analytics.contrato import (
    Consulta,
    HuellaBD,
    Parametro,
    ResultadoNecesidad,
    Universo,
    ValorClave,
    limitar_tabla,
)

ID = "N1"
TITULO = "Recompra por producto"
PREGUNTA = "¿Qué productos se recompran de forma consistente y cuáles se prueban y se abandonan?"
DECISION = "Qué asegurar en inventario (alta recompra y volumen) y qué revisar o retirar (alto abandono, baja recompra)."

MIN_LINEAS_DEFAULT = 50
MIN_OBSERVABLES_DEFAULT = 30
PERCENTIL_ALTO_DEFAULT = 0.75
PERCENTIL_BAJO_DEFAULT = 0.25

JUSTIF_MIN_LINEAS = (
    "Medido sobre la distribución real de líneas por producto (min=1, P25=18, "
    "mediana=63, P75=272, P90=1066, máx=491,291): MIN_LINEAS=50 conserva 27,208 "
    "de 49,688 productos (54.7%) y el 98.7% de las líneas totales, descartando "
    "la cola larga de productos con muestra insuficiente para estimar una tasa "
    "de recompra estable, sin perder volumen significativo."
)
JUSTIF_MIN_OBSERVABLES = (
    "Medido sobre la distribución de pares (usuario,producto) observables por "
    "producto con K_POST=5 (min=0, P25=7, mediana=24, máx=52,255): "
    "MIN_OBSERVABLES=30 deja 22,513 de 49,685 productos (45.3%) con una muestra "
    "mínima razonable para estimar una tasa de abandono (evita productos con "
    "2-3 pares observables, donde un solo caso mueve la tasa 33-50 puntos)."
)
JUSTIF_PERCENTILES = (
    "P75/P25 son los cortes estándar de cuartil superior/inferior; se aplican "
    "sobre los productos elegibles, no sobre todo el catálogo, para que el "
    "corte no esté dominado por productos de muestra insuficiente."
)


def _sql_base() -> str:
    return """
    CREATE TEMP TABLE n1_base AS
    SELECT n.product_id, n.lineas, n.usuarios_distintos, n.lineas_recompra,
           n.tasa_recompra, n.observables, n.abandonos, n.tasa_abandono,
           p.product_name,
           a.aisle, (p.aisle_id IN (100, 6)) AS aisle_missing_other,
           d.department, (p.department_id IN (21, 2)) AS department_missing_other
    FROM agg.agg_n1_producto n
    JOIN products p ON p.product_id = n.product_id
    JOIN aisles a ON a.aisle_id = p.aisle_id
    JOIN departments d ON d.department_id = p.department_id
    """


def calcular(
    con: duckdb.DuckDBPyConnection,
    agg_path: Path,
    *,
    min_lineas: int = MIN_LINEAS_DEFAULT,
    min_observables: int = MIN_OBSERVABLES_DEFAULT,
    percentil_alto: float = PERCENTIL_ALTO_DEFAULT,
    percentil_bajo: float = PERCENTIL_BAJO_DEFAULT,
) -> ResultadoNecesidad:
    t_inicio = time.perf_counter()

    con_agg = abrir_agregado(agg_path, con)
    meta = dict(con_agg.execute("SELECT clave, valor FROM agg_meta").fetchall())
    k_post = int(meta["n1_k_post"])
    con_agg.close()

    consultas: list[Consulta] = []

    con.execute(f"ATTACH '{Path(agg_path).as_posix()}' AS agg (READ_ONLY)")
    try:
        sql_base = _sql_base()
        con.execute(sql_base)
        consultas.append(Consulta("base_producto", sql_base.strip()))

        sql_global = "SELECT SUM(lineas), SUM(lineas_recompra), SUM(lineas_recompra) * 1.0 / SUM(lineas), COUNT(*), SUM(observables) * 1.0 / SUM(usuarios_distintos) FROM n1_base"
        lineas_total, lineas_recompra_total, tasa_global, productos_total, pct_observable_global = con.execute(sql_global).fetchone()
        consultas.append(Consulta("global", sql_global))

        sql_percentiles = f"""
        WITH elegibles AS (
            SELECT * FROM n1_base WHERE lineas >= {min_lineas} AND observables >= {min_observables}
        )
        SELECT COUNT(*), SUM(lineas),
               PERCENTILE_CONT({percentil_alto}) WITHIN GROUP (ORDER BY lineas),
               PERCENTILE_CONT({percentil_alto}) WITHIN GROUP (ORDER BY tasa_recompra),
               PERCENTILE_CONT({percentil_bajo}) WITHIN GROUP (ORDER BY tasa_recompra),
               PERCENTILE_CONT({percentil_alto}) WITHIN GROUP (ORDER BY tasa_abandono)
        FROM elegibles
        """
        (n_elegibles, lineas_elegibles, p_alto_lineas, p_alto_tasa_recompra,
         p_bajo_tasa_recompra, p_alto_tasa_abandono) = con.execute(sql_percentiles).fetchone()
        lineas_elegibles = lineas_elegibles or 0
        consultas.append(Consulta("percentiles_elegibles", sql_percentiles.strip()))

        cols = ["product_id", "product_name", "aisle", "department", "lineas", "usuarios_distintos",
                "tasa_recompra", "observables", "abandonos", "tasa_abandono",
                "aisle_missing_other", "department_missing_other",
                "asegurar_inventario", "revisar_retirar"]
        if n_elegibles == 0:
            # Sin productos elegibles (universo muy chico o parámetros muy
            # estrictos) los percentiles son NULL: no hay nada que clasificar,
            # pero no rompemos la consulta con literales NULL en el WHERE.
            filas = []
            sql_clasificacion = (
                "-- n_elegibles=0: no se ejecutó clasificación (percentiles indefinidos sobre un conjunto vacío)"
            )
        else:
            sql_clasificacion = f"""
            WITH elegibles AS (
                SELECT * FROM n1_base WHERE lineas >= {min_lineas} AND observables >= {min_observables}
            )
            SELECT product_id, product_name, aisle, department, lineas, usuarios_distintos,
                   tasa_recompra, observables, abandonos, tasa_abandono,
                   aisle_missing_other, department_missing_other,
                   (lineas >= {p_alto_lineas} AND tasa_recompra >= {p_alto_tasa_recompra}) AS asegurar_inventario,
                   (tasa_abandono >= {p_alto_tasa_abandono} AND tasa_recompra <= {p_bajo_tasa_recompra}) AS revisar_retirar
            FROM elegibles
            ORDER BY tasa_recompra DESC, lineas DESC, product_id ASC
            """
            filas = [dict(zip(cols, r)) for r in con.execute(sql_clasificacion).fetchall()]
        consultas.append(Consulta("clasificacion", sql_clasificacion.strip()))
    finally:
        con.execute("DROP TABLE IF EXISTS n1_base")
        con.execute("DETACH agg")

    asegurar = [f for f in filas if f["asegurar_inventario"]]
    revisar = [f for f in filas if f["revisar_retirar"]]
    revisar_missing_other = [f for f in revisar if f["aisle_missing_other"] or f["department_missing_other"]]

    tabla_asegurar, trunc_a = limitar_tabla(asegurar)
    tabla_revisar, trunc_r = limitar_tabla(revisar)

    tablas = {
        "asegurar_inventario": tabla_asegurar,
        "revisar_retirar": tabla_revisar,
    }
    if n_elegibles == 0:
        tablas_criterio_corte = {
            "asegurar_inventario": f"Sin productos elegibles (líneas>={min_lineas}, observables>={min_observables})",
            "revisar_retirar": f"Sin productos elegibles (líneas>={min_lineas}, observables>={min_observables})",
        }
    else:
        tablas_criterio_corte = {
            "asegurar_inventario": (
                f"Elegibles (líneas>={min_lineas}, observables>={min_observables}) con "
                f"lineas>=P{int(percentil_alto*100)} ({p_alto_lineas:.0f}) y tasa_recompra>=P{int(percentil_alto*100)} "
                f"({p_alto_tasa_recompra:.3f}), orden tasa_recompra desc, lineas desc, product_id asc"
                + (f"; truncado a 500 de {len(asegurar)}" if trunc_a else "")
            ),
            "revisar_retirar": (
                f"Elegibles con tasa_abandono>=P{int(percentil_alto*100)} ({p_alto_tasa_abandono:.3f}) y "
                f"tasa_recompra<=P{int(percentil_bajo*100)} ({p_bajo_tasa_recompra:.3f}), "
                f"orden tasa_recompra desc, lineas desc, product_id asc"
                + (f"; truncado a 500 de {len(revisar)}" if trunc_r else "")
            ),
        }

    hallazgos = [
        f"La tasa global de recompra sobre todas las líneas es {tasa_global:.1%} "
        f"({lineas_recompra_total:,} de {lineas_total:,} líneas con reordered=1).",
        f"De {productos_total:,} productos, {n_elegibles:,} ({n_elegibles/productos_total:.1%}) "
        f"cumplen el mínimo de volumen (>={min_lineas} líneas) y de pares observables "
        f"(>={min_observables}) para clasificarse; cubren {lineas_elegibles:,} líneas "
        f"({lineas_elegibles/lineas_total:.1%} del total).",
        f"{len(asegurar):,} productos elegibles caen en 'Asegurar inventario' "
        f"(volumen y tasa de recompra en el percentil {int(percentil_alto*100)} o más entre elegibles).",
        f"{len(revisar):,} productos elegibles caen en 'Revisar/retirar' (tasa de abandono en "
        f"el percentil {int(percentil_alto*100)} o más y tasa de recompra en el percentil "
        f"{int(percentil_bajo*100)} o menos entre elegibles).",
        f"De los productos en 'Revisar/retirar', {len(revisar_missing_other):,} pertenecen a un "
        f"pasillo o departamento 'missing'/'other' (se marcan con una columna, no se excluyen).",
    ]

    limitaciones = [
        "No hay precios en el dataset: esta clasificación es de volumen y patrón de recompra, "
        "no dice nada sobre ingresos ni ganancia.",
        f"El abandono se mide con control de censura: un par (usuario,producto) solo se "
        f"considera 'observable' si el usuario tuvo al menos K_POST={k_post} pedidos con líneas "
        f"después de su primera compra del producto. Globalmente, {pct_observable_global:.1%} de "
        f"los pares (usuario,producto) son observables con este K_POST; el resto se excluye del "
        f"cálculo de tasa_abandono (no se cuenta como 'no abandonó' ni como 'abandonó').",
        "Productos con pasillo/departamento 'missing' u 'other' se marcan con una columna "
        "pero no se excluyen de la clasificación: podrían sesgar 'Revisar/retirar' si son muchos.",
        f"`agg_n1_producto` se precalculó en `{Path(agg_path).name}` (K_POST se fija al construir "
        "el agregado; cambiarlo exige `python -m src.analytics.agregados --rebuild`).",
    ]

    huella = huella_bd(con)
    duracion = time.perf_counter() - t_inicio

    return ResultadoNecesidad(
        id=ID,
        titulo=TITULO,
        pregunta=PREGUNTA,
        decision=DECISION,
        universo=Universo(
            texto="Líneas de la vista order_products (prior+train) unidas a products/aisles/departments.",
            filtros=["order_products__prior UNION ALL order_products__train (universo de líneas)"],
        ),
        parametros=[
            Parametro("MIN_LINEAS", min_lineas, JUSTIF_MIN_LINEAS),
            Parametro("MIN_OBSERVABLES", min_observables, JUSTIF_MIN_OBSERVABLES),
            Parametro("K_POST", k_post,
                      "Fijado al construir agg_n1_producto (ver docs/FASE2A_REPORTE.md): con K_POST=3 "
                      "el 80.2% de los pares son observables, con K_POST=5 el 67.2%, con K_POST=10 el "
                      "46.1%. Se eligió 5 porque retiene alta potencia estadística sin exigir una "
                      "ventana de recompra irrealmente larga frente a la mediana de 10 pedidos por usuario."),
            Parametro("percentil_alto", percentil_alto, JUSTIF_PERCENTILES),
            Parametro("percentil_bajo", percentil_bajo, JUSTIF_PERCENTILES),
        ],
        consultas=consultas,
        tablas=tablas,
        tablas_criterio_corte=tablas_criterio_corte,
        valores_clave=[
            ValorClave("Tasa global de recompra", tasa_global, "fracción", "global"),
            ValorClave("Productos elegibles", n_elegibles, "productos", "percentiles_elegibles"),
            ValorClave("Tamaño 'Asegurar inventario'", len(asegurar), "productos", "clasificacion"),
            ValorClave("Tamaño 'Revisar/retirar'", len(revisar), "productos", "clasificacion"),
            ValorClave("% pares observables (censura)", pct_observable_global, "fracción", "global"),
        ],
        hallazgos=hallazgos,
        limitaciones=limitaciones,
        duracion_s=duracion,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella),
    )
