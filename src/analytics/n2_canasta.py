"""N2 — Canasta: pares de productos co-ocurrentes.

Pregunta: ¿qué pares de productos se compran juntos con más frecuencia, y
cuáles tienen una asociación fuerte aunque sean menos frecuentes?
Decisión: paquetes, promociones cruzadas, "te puede interesar".

Universo: pedidos con líneas (eval_set IN ('prior','train')), restringido a
un subconjunto de PRODUCTOS candidatos (no de pedidos) con soporte propio
>= MIN_SOPORTE_PRODUCTO.

Este módulo SOLO LEE `agg_pares` (precalculado por `src.analytics.
agregados.construir_agregados`): el armado completo de pares no cabe en un
panel interactivo (varios segundos y GBs de RAM incluso restringido a
productos frecuentes) — ver la tabla de compromiso medida en
`docs/FASE2A_REPORTE.md`. El armado NO vive aquí a propósito.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.analytics.agregados import MIN_PARES_LIFT_DEFAULT, abrir_agregado
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

ID = "N2"
TITULO = "Canasta: pares de productos"
PREGUNTA = "¿Qué pares de productos se compran juntos con más frecuencia, y cuáles tienen una asociación fuerte aunque sean poco frecuentes?"
DECISION = "Paquetes, promociones cruzadas y sugerencias tipo 'te puede interesar'."

JUSTIF_MIN_SOPORTE_PRODUCTO = (
    "Tabla de compromiso medida (docs/FASE2A_REPORTE.md), armado aislado por valor: "
    "0.0005 -> 3403 productos candidatos, 75.20% de cobertura, RSS pico ~3.9GB (EXCEDE "
    "el límite de 3GB); 0.001 -> 1776 productos, 63.86%, RSS ~2.98GB (al límite); "
    "0.002 -> 842 productos, 51.05%, RSS ~1.7GB (elegido: margen claro bajo el límite); "
    "0.003 -> 510 productos, 43.13%, RSS ~1.35GB (más barato, cubre menos). Se eligió "
    "0.002 como el punto que deja margen bajo el presupuesto de 3GB sin sacrificar "
    "cobertura de forma drástica."
)


def calcular(
    con: duckdb.DuckDBPyConnection,
    agg_path: Path,
    *,
    min_pares_lift: int = MIN_PARES_LIFT_DEFAULT,
) -> ResultadoNecesidad:
    t_inicio = time.perf_counter()

    con_agg = abrir_agregado(agg_path, con)
    meta = dict(con_agg.execute("SELECT clave, valor FROM agg_meta").fetchall())
    con_agg.close()

    min_soporte_producto = float(meta["n2_min_soporte_producto"])
    n_candidatos = int(meta["n2_n_candidatos"])
    n_pares = int(meta["n2_n_pares"])
    pct_cobertura = float(meta["n2_pct_cobertura_pedidos_producto"])
    duracion_armado = float(meta["n2_duracion_construccion_s"])

    consultas: list[Consulta] = []
    con.execute(f"ATTACH '{Path(agg_path).as_posix()}' AS agg (READ_ONLY)")
    try:
        sql_top_frecuentes = """
        SELECT ap.producto_a, pa.product_name AS producto_a_nombre,
               ap.producto_b, pb.product_name AS producto_b_nombre,
               ap.pedidos_con_par, ap.soporte, ap.confianza_a_b, ap.confianza_b_a, ap.lift
        FROM agg.agg_pares ap
        JOIN products pa ON pa.product_id = ap.producto_a
        JOIN products pb ON pb.product_id = ap.producto_b
        ORDER BY ap.pedidos_con_par DESC, ap.producto_a ASC, ap.producto_b ASC
        LIMIT 50
        """
        cols = ["producto_a", "producto_a_nombre", "producto_b", "producto_b_nombre",
                "pedidos_con_par", "soporte", "confianza_a_b", "confianza_b_a", "lift"]
        top_frecuentes = [dict(zip(cols, r)) for r in con.execute(sql_top_frecuentes).fetchall()]
        consultas.append(Consulta("top_50_pedidos_con_par", sql_top_frecuentes.strip()))

        sql_top_lift = f"""
        SELECT ap.producto_a, pa.product_name AS producto_a_nombre,
               ap.producto_b, pb.product_name AS producto_b_nombre,
               ap.pedidos_con_par, ap.soporte, ap.confianza_a_b, ap.confianza_b_a, ap.lift
        FROM agg.agg_pares ap
        JOIN products pa ON pa.product_id = ap.producto_a
        JOIN products pb ON pb.product_id = ap.producto_b
        WHERE ap.pedidos_con_par >= {min_pares_lift}
        ORDER BY ap.lift DESC, ap.pedidos_con_par DESC, ap.producto_a ASC, ap.producto_b ASC
        LIMIT 50
        """
        top_lift = [dict(zip(cols, r)) for r in con.execute(sql_top_lift).fetchall()]
        consultas.append(Consulta("top_50_lift", sql_top_lift.strip()))

        sql_dist_pares = "SELECT MIN(pedidos_con_par), MEDIAN(pedidos_con_par), PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY pedidos_con_par), PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY pedidos_con_par), MAX(pedidos_con_par), COUNT(*) FILTER (WHERE pedidos_con_par >= " + str(min_pares_lift) + ") FROM agg.agg_pares"
        dmin, dmed, dp75, dp90, dmax, n_sobre_umbral = con.execute(sql_dist_pares).fetchone()
        consultas.append(Consulta("distribucion_pedidos_con_par", sql_dist_pares))
    finally:
        con.execute("DETACH agg")

    tabla_frecuentes, _ = limitar_tabla(top_frecuentes)
    tabla_lift, _ = limitar_tabla(top_lift)

    hallazgos = [
        f"El universo de candidatos son {n_candidatos:,} productos con soporte propio >= "
        f"{min_soporte_producto:.1%} (aparecen en al menos {min_soporte_producto:.1%} de los "
        f"pedidos con líneas), que cubren {pct_cobertura:.1%} de las incidencias "
        f"producto-pedido. El armado generó {n_pares:,} pares en {duracion_armado:.1f}s.",
    ]
    if top_frecuentes:
        hallazgos.append(
            f"El par más frecuente es "
            f"{top_frecuentes[0]['producto_a_nombre']!r} + {top_frecuentes[0]['producto_b_nombre']!r} "
            f"con {top_frecuentes[0]['pedidos_con_par']:,} pedidos en común "
            f"(soporte={top_frecuentes[0]['soporte']:.4f})."
        )
    if top_lift:
        hallazgos.append(
            f"Entre los {n_sobre_umbral:,} pares con pedidos_con_par >= MIN_PARES_LIFT="
            f"{min_pares_lift}, el de mayor lift es "
            f"{top_lift[0]['producto_a_nombre']!r} + {top_lift[0]['producto_b_nombre']!r} "
            f"(lift={top_lift[0]['lift']:.2f}, {top_lift[0]['pedidos_con_par']:,} pedidos en común)."
        )
    else:
        hallazgos.append(
            f"Ningún par alcanza pedidos_con_par >= MIN_PARES_LIFT={min_pares_lift} "
            "en este universo: no se puede construir la tabla de lift."
        )

    limitaciones = [
        "El lift favorece pares raros: un par con pocos pedidos en común puede tener lift muy "
        "alto si ambos productos son individualmente poco frecuentes. Por eso la tabla de lift "
        f"se restringe a pares con pedidos_con_par >= MIN_PARES_LIFT={min_pares_lift} (mediana de "
        f"pedidos_con_par entre candidatos: {dmed:.0f}, P75={dp75:.0f}, P90={dp90:.0f}, "
        f"máx={dmax:,}) — sin ese piso, la tabla de lift estaría dominada por ruido estadístico.",
        "Los productos muy populares dominan la tabla de 'pedidos_con_par' por conteo bruto, no "
        "porque su asociación sea especialmente fuerte (ver confianza/lift de esos mismos pares).",
        f"El universo de productos analizados está restringido a los {n_candidatos:,} con soporte "
        f"propio >= {min_soporte_producto} ({pct_cobertura:.1%} de las incidencias producto-pedido): "
        "es una decisión de diseño para que el armado quepa en el presupuesto de tiempo/RAM de esta "
        "fase, no el universo completo de 49,688 productos.",
        "No hay precios: 'canasta' aquí es solo co-ocurrencia de productos, no valor de la compra conjunta.",
    ]

    huella = huella_bd(con)
    duracion = time.perf_counter() - t_inicio

    return ResultadoNecesidad(
        id=ID,
        titulo=TITULO,
        pregunta=PREGUNTA,
        decision=DECISION,
        universo=Universo(
            texto="Pedidos con líneas (eval_set IN ('prior','train')), pares no ordenados de "
                  "productos candidatos (soporte propio >= MIN_SOPORTE_PRODUCTO) en el mismo pedido.",
            filtros=[f"producto candidato: soporte propio >= {min_soporte_producto}",
                     "pedidos con eval_set IN ('prior','train')"],
        ),
        parametros=[
            Parametro("MIN_SOPORTE_PRODUCTO", min_soporte_producto, JUSTIF_MIN_SOPORTE_PRODUCTO),
            Parametro("MIN_PARES_LIFT", min_pares_lift,
                      f"Medido sobre la distribución de pedidos_con_par entre los {n_pares:,} pares "
                      f"candidatos (mediana={dmed:.0f}, P75={dp75:.0f}, P90={dp90:.0f}, máx={dmax:,}): "
                      f"{min_pares_lift} retiene {n_sobre_umbral:,} pares "
                      f"({n_sobre_umbral/n_pares:.1%}) con una muestra por encima de la mediana, evitando "
                      "que 1-2 co-ocurrencias produzcan un lift extremo no representativo."),
        ],
        consultas=consultas,
        tablas={"top_50_pedidos_con_par": tabla_frecuentes, "top_50_lift": tabla_lift},
        tablas_criterio_corte={
            "top_50_pedidos_con_par": "Top 50 por pedidos_con_par desc, desempate producto_a/producto_b asc",
            "top_50_lift": f"Entre pares con pedidos_con_par>={min_pares_lift}, top 50 por lift desc, "
                           "desempate pedidos_con_par desc, producto_a/producto_b asc",
        },
        valores_clave=[
            ValorClave("Productos candidatos", n_candidatos, "productos", "distribucion_pedidos_con_par"),
            ValorClave("% cobertura de incidencias producto-pedido", pct_cobertura, "fracción", "distribucion_pedidos_con_par"),
            ValorClave("Pares generados", n_pares, "pares", "distribucion_pedidos_con_par"),
            ValorClave("Tiempo de armado del agregado", duracion_armado, "segundos", "distribucion_pedidos_con_par"),
            ValorClave("Pares con pedidos_con_par >= MIN_PARES_LIFT", n_sobre_umbral, "pares", "distribucion_pedidos_con_par"),
        ],
        hallazgos=hallazgos,
        limitaciones=limitaciones,
        duracion_s=duracion,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella),
    )
