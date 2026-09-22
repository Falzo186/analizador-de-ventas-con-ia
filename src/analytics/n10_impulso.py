"""N10 — Final del carrito / impulso.

Pregunta: ¿qué productos se agregan sistemáticamente al final del carrito,
más de lo que el tamaño del pedido explicaría?
Decisión: qué poner cerca de "caja" (pantalla final) o como sugerencia de
último momento.

Universo: líneas de pedidos con tamaño >= MIN_TAM (pedidos de 1 solo
producto no tienen "posición relativa" — dividir por (tamaño-1)=0 no está
definido). Usa `agg_n10_producto` (precalculado, ver `src.analytics.
agregados`: un COUNT(DISTINCT user_id) por producto sobre estas líneas mide
varios segundos, igual que en N1/N5).
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

ID = "N10"
TITULO = "Final del carrito / impulso"
PREGUNTA = "¿Qué productos aparecen sistemáticamente al final del carrito más de lo que el tamaño del pedido explicaría?"
DECISION = "Qué poner cerca de 'caja' o como sugerencia de último momento."

MIN_APARICIONES_DEFAULT = 50
MIN_USUARIOS_DEFAULT = 10
PERCENTIL_TASA_BAJA_DEFAULT = 0.25


def calcular(
    con: duckdb.DuckDBPyConnection,
    agg_path: Path,
    *,
    min_apariciones: int = MIN_APARICIONES_DEFAULT,
    min_usuarios: int = MIN_USUARIOS_DEFAULT,
    percentil_tasa_baja: float = PERCENTIL_TASA_BAJA_DEFAULT,
) -> ResultadoNecesidad:
    t_inicio = time.perf_counter()
    consultas: list[Consulta] = []

    con_agg = abrir_agregado(agg_path, con)
    meta = dict(con_agg.execute("SELECT clave, valor FROM agg_meta").fetchall())
    con_agg.close()
    min_tam = int(meta["n10_min_tam"])

    con.execute(f"ATTACH '{Path(agg_path).as_posix()}' AS agg (READ_ONLY)")
    try:
        sql_cobertura = f"""
        SELECT
            SUM(tamano) FILTER (WHERE tamano >= {min_tam}) AS lineas_incluidas,
            SUM(tamano) FILTER (WHERE tamano < {min_tam}) AS lineas_excluidas,
            COUNT(*) FILTER (WHERE tamano >= {min_tam}) AS pedidos_incluidos,
            COUNT(*) FILTER (WHERE tamano < {min_tam}) AS pedidos_excluidos
        FROM agg.agg_pedido
        """
        lineas_inc, lineas_exc, pedidos_inc, pedidos_exc = con.execute(sql_cobertura).fetchone()
        consultas.append(Consulta("cobertura_min_tam", sql_cobertura.strip()))

        # Una sola pasada por las ~33.6M líneas (tramo x departamento); la
        # tabla "solo tramo" se deriva sumando esto en Python, para no
        # repetir el join pesado dos veces (el join op+agg_pedido+products
        # es el costo dominante de esta necesidad).
        sql_tramo_depto = f"""
        WITH lineas AS (
            SELECT op.reordered, p.department_id,
                   (op.add_to_cart_order - 1.0) / (ap.tamano - 1) AS pos_norm
            FROM order_products op
            JOIN agg.agg_pedido ap ON ap.order_id = op.order_id
            JOIN products p ON p.product_id = op.product_id
            WHERE ap.tamano >= {min_tam}
        ),
        con_tramo AS (
            SELECT reordered, department_id,
                   LEAST(CAST(FLOOR(pos_norm * 5) AS INTEGER), 4) AS tramo
            FROM lineas
        )
        SELECT ct.tramo, d.department, COUNT(*) AS lineas, SUM(ct.reordered) * 1.0 / COUNT(*) AS tasa_recompra
        FROM con_tramo ct JOIN departments d ON d.department_id = ct.department_id
        GROUP BY ct.tramo, d.department ORDER BY ct.tramo, d.department
        """
        tramo_depto = [
            {"tramo": t, "department": dep, "lineas": n, "tasa_recompra": tr}
            for t, dep, n, tr in con.execute(sql_tramo_depto).fetchall()
        ]
        consultas.append(Consulta("tasa_recompra_por_tramo_y_departamento", sql_tramo_depto.strip()))

        acumulado: dict[int, list[float]] = {}
        for fila in tramo_depto:
            n, tr = fila["lineas"], fila["tasa_recompra"]
            l, r = acumulado.setdefault(fila["tramo"], [0, 0.0])
            acumulado[fila["tramo"]] = [l + n, r + n * tr]
        tramos = [
            {"tramo": t, "lineas": vals[0], "tasa_recompra": vals[1] / vals[0]}
            for t, vals in sorted(acumulado.items())
        ]

        sql_productos = """
        SELECT n.product_id, p.product_name, n.apariciones, n.apariciones_ultimo_tramo,
               n.apariciones_ultimo_tramo * 1.0 / n.apariciones AS share_final,
               n.esperado_final,
               (n.apariciones_ultimo_tramo * 1.0 / n.apariciones) / n.esperado_final AS indice_final,
               n.usuarios_distintos, n.lineas_recompra * 1.0 / n.apariciones AS tasa_recompra
        FROM agg.agg_n10_producto n JOIN products p ON p.product_id = n.product_id
        """
        con.execute(f"CREATE TEMP TABLE n10_completo AS {sql_productos}")
        consultas.append(Consulta("completo_producto", sql_productos.strip()))

        sql_top_final = """
        SELECT product_id, product_name, apariciones, apariciones_ultimo_tramo, share_final,
               esperado_final, indice_final, usuarios_distintos, tasa_recompra
        FROM n10_completo
        ORDER BY indice_final DESC, apariciones DESC, product_id ASC
        LIMIT 50
        """
        cols = ["product_id", "product_name", "apariciones", "apariciones_ultimo_tramo", "share_final",
                "esperado_final", "indice_final", "usuarios_distintos", "tasa_recompra"]
        top_final = [dict(zip(cols, r)) for r in con.execute(sql_top_final).fetchall()]
        consultas.append(Consulta("top_50_indice_final", sql_top_final.strip()))

        sql_elegibles = f"""
        CREATE TEMP TABLE n10_elegibles AS
        SELECT * FROM n10_completo
        WHERE apariciones >= {min_apariciones} AND usuarios_distintos >= {min_usuarios}
        """
        con.execute(sql_elegibles)
        n_elegibles = con.execute("SELECT COUNT(*) FROM n10_elegibles").fetchone()[0]
        consultas.append(Consulta("elegibles", sql_elegibles.strip()))

        sql_p25_tasa = "SELECT PERCENTILE_CONT(?) WITHIN GROUP (ORDER BY tasa_recompra) FROM n10_elegibles"
        p_tasa_baja = con.execute(sql_p25_tasa, [percentil_tasa_baja]).fetchone()[0]
        consultas.append(Consulta("percentil_tasa_baja", sql_p25_tasa.replace("?", str(percentil_tasa_baja))))

        if n_elegibles == 0 or p_tasa_baja is None:
            # Sin elegibles, el percentil sobre un conjunto vacío es NULL:
            # no hay nada que clasificar, no rompemos la consulta con un
            # literal NULL en el WHERE.
            top_final_tasa_baja = []
            sql_top_final_tasa_baja = "-- n_elegibles=0: no se ejecutó (percentil indefinido sobre un conjunto vacío)"
        else:
            sql_top_final_tasa_baja = f"""
            SELECT product_id, product_name, apariciones, apariciones_ultimo_tramo, share_final,
                   esperado_final, indice_final, usuarios_distintos, tasa_recompra
            FROM n10_elegibles
            WHERE tasa_recompra <= {p_tasa_baja}
            ORDER BY indice_final DESC, apariciones DESC, product_id ASC
            LIMIT 50
            """
            top_final_tasa_baja = [dict(zip(cols, r)) for r in con.execute(sql_top_final_tasa_baja).fetchall()]
        consultas.append(Consulta("top_50_indice_final_tasa_baja", sql_top_final_tasa_baja.strip()))

        con.execute("DROP TABLE n10_elegibles")
        con.execute("DROP TABLE n10_completo")
    finally:
        con.execute("DETACH agg")

    tabla_top, _ = limitar_tabla(top_final)
    tabla_top_baja, _ = limitar_tabla(top_final_tasa_baja)

    hallazgos = [
        f"El universo incluye {lineas_inc:,} líneas de {pedidos_inc:,} pedidos con tamaño >= "
        f"MIN_TAM={min_tam}; se excluyen {lineas_exc:,} líneas de {pedidos_exc:,} pedidos de tamaño "
        f"menor (sin posición relativa definida).",
        f"Tasa de recompra por tramo (0=inicio del carrito, 4=último quinto): "
        + ", ".join(f"tramo {t['tramo']}: {t['tasa_recompra']:.1%}" for t in tramos) + ".",
        f"{n_elegibles:,} productos cumplen MIN_APARICIONES>={min_apariciones} y "
        f"MIN_USUARIOS>={min_usuarios}.",
    ]
    if top_final:
        hallazgos.append(
            f"El producto con mayor índice_final es {top_final[0]['product_name']!r} "
            f"(índice_final={top_final[0]['indice_final']:.2f}x lo esperado por tamaño de pedido, "
            f"share_final={top_final[0]['share_final']:.1%})."
        )
    if top_final_tasa_baja:
        hallazgos.append(
            f"Entre elegibles con tasa_recompra en el percentil {int(percentil_tasa_baja*100)} o menos "
            f"(<= {p_tasa_baja:.1%}), el de mayor índice_final es "
            f"{top_final_tasa_baja[0]['product_name']!r} (índice_final={top_final_tasa_baja[0]['indice_final']:.2f}x, "
            f"tasa_recompra={top_final_tasa_baja[0]['tasa_recompra']:.1%}): candidato a impulso puro, no a "
            "recompra habitual."
        )

    limitaciones = [
        "El orden de agregado al carrito puede reflejar la interfaz de compra (listas de 'comprar "
        "de nuevo' que se agregan primero, dejando lo demás al final por descarte), no "
        "necesariamente una decisión deliberada de 'agregar esto al final'. 'Impulso' aquí es una "
        "hipótesis a validar, no un hecho.",
        "No hay precios: no se afirma nada sobre el valor de la compra de impulso, solo sobre la "
        "posición relativa en el carrito.",
        f"MIN_TAM={min_tam} excluye pedidos de un solo producto (pos_norm no está definido, "
        "dividir por tamaño-1=0).",
    ]

    huella = huella_bd(con)
    duracion = time.perf_counter() - t_inicio

    return ResultadoNecesidad(
        id=ID,
        titulo=TITULO,
        pregunta=PREGUNTA,
        decision=DECISION,
        universo=Universo(
            texto=f"Líneas de pedidos con tamaño >= MIN_TAM={min_tam}.",
            filtros=[f"tamano >= {min_tam}"],
        ),
        parametros=[
            Parametro("MIN_TAM", min_tam, f"Mínimo 2 pedido por definición (pos_norm indefinido en "
                                            f"pedidos de 1 producto). Cobertura medida: {pedidos_inc:,} de "
                                            f"{pedidos_inc+pedidos_exc:,} pedidos ({pedidos_inc/(pedidos_inc+pedidos_exc):.1%}), "
                                            f"{lineas_inc:,} de {lineas_inc+lineas_exc:,} líneas "
                                            f"({lineas_inc/(lineas_inc+lineas_exc):.1%})."),
            Parametro("MIN_APARICIONES", min_apariciones, "Igual criterio que N1/N5: muestra mínima "
                                                             "para estimar share_final/índice_final de forma estable."),
            Parametro("MIN_USUARIOS", min_usuarios, "Evita que la señal de 'producto de cierre' venga "
                                                       "de muy pocos usuarios recurrentes."),
            Parametro("percentil_tasa_baja", percentil_tasa_baja, "Cuartil inferior de tasa_recompra "
                                                                     "entre elegibles, para aislar candidatos de impulso puro (no recompra habitual)."),
        ],
        consultas=consultas,
        tablas={
            "tasa_recompra_por_tramo": tramos,
            "tasa_recompra_por_tramo_y_departamento": tramo_depto,
            "top_50_indice_final": tabla_top,
            "top_50_indice_final_tasa_baja": tabla_top_baja,
        },
        tablas_criterio_corte={
            "tasa_recompra_por_tramo": "Los 5 tramos de pos_norm, orden tramo asc",
            "tasa_recompra_por_tramo_y_departamento": "5 tramos x 21 departamentos, orden tramo, departamento asc",
            "top_50_indice_final": "Top 50 por índice_final desc (todos los productos), desempate apariciones desc, product_id asc",
            "top_50_indice_final_tasa_baja": f"Entre elegibles con tasa_recompra<=P{int(percentil_tasa_baja*100)}, "
                                              "top 50 por índice_final desc, desempate apariciones desc, product_id asc",
        },
        valores_clave=[
            ValorClave("Líneas incluidas", lineas_inc, "líneas", "cobertura_min_tam"),
            ValorClave("Líneas excluidas (MIN_TAM)", lineas_exc, "líneas", "cobertura_min_tam"),
            ValorClave("Productos elegibles", n_elegibles, "productos", "elegibles"),
            ValorClave("Producto top índice_final", top_final[0]["product_name"] if top_final else None, "producto", "top_50_indice_final"),
        ],
        hallazgos=hallazgos,
        limitaciones=limitaciones,
        duracion_s=duracion,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella),
    )
