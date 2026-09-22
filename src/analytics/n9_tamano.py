"""N9 — Tamaño de pedido.

Pregunta: ¿cómo se distribuye el tamaño del pedido y qué factores se asocian
con pedidos más grandes o más chicos?
Decisión: umbral de incentivo/envío gratis expresado en NÚMERO DE PRODUCTOS
(no hay precios en el dataset, así que nunca en montos). Descriptivo, no causal.

Universo: pedidos con líneas (`agg_pedido`, precalculado).
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

ID = "N9"
TITULO = "Tamaño de pedido"
PREGUNTA = "¿Cómo se distribuye el tamaño del pedido y qué factores se asocian con pedidos más grandes o más chicos?"
DECISION = "Umbral de incentivo expresado en número de productos (sin montos: el dataset no tiene precios)."

MIN_PEDIDOS_DEFAULT = 5
K_CANDIDATOS_DEFAULT = (5, 10, 15, 20)

_SQL_BUCKET_INTERVALO = """
CASE
    WHEN days_since_prior_order IS NULL THEN 'sin_intervalo_previo'
    WHEN days_since_prior_order <= 3 THEN '0-3'
    WHEN days_since_prior_order <= 7 THEN '4-7'
    WHEN days_since_prior_order <= 14 THEN '8-14'
    WHEN days_since_prior_order <= 29 THEN '15-29'
    ELSE '30_censurado'
END
"""

_SQL_BUCKET_K = """
CASE
    WHEN k = 1 THEN '1'
    WHEN k <= 5 THEN '2-5'
    WHEN k <= 10 THEN '6-10'
    WHEN k <= 20 THEN '11-20'
    WHEN k <= 50 THEN '21-50'
    ELSE '51-100'
END
"""


def calcular(
    con: duckdb.DuckDBPyConnection,
    agg_path: Path,
    *,
    min_pedidos: int = MIN_PEDIDOS_DEFAULT,
    k_candidatos: tuple[int, ...] = K_CANDIDATOS_DEFAULT,
) -> ResultadoNecesidad:
    t_inicio = time.perf_counter()
    consultas: list[Consulta] = []

    con_agg = abrir_agregado(agg_path, con)
    con_agg.close()
    con.execute(f"ATTACH '{Path(agg_path).as_posix()}' AS agg (READ_ONLY)")

    try:
        sql_stats = """
        SELECT COUNT(*), SUM(tamano), AVG(tamano), MEDIAN(tamano), MODE(tamano),
               PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY tamano),
               PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY tamano),
               PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY tamano)
        FROM agg.agg_pedido
        """
        (total_pedidos, total_lineas, media, mediana, moda, p75, p90, p99) = con.execute(sql_stats).fetchone()
        consultas.append(Consulta("estadisticos_globales", sql_stats.strip()))
        tam_max = int(round(p99))

        sql_histograma = f"""
        SELECT CASE WHEN tamano > {tam_max} THEN {tam_max + 1} ELSE tamano END AS tamano_bin,
               COUNT(*) AS pedidos
        FROM agg.agg_pedido
        GROUP BY 1 ORDER BY 1
        """
        histograma = [{"tamano_bin": t, "pedidos": p, "es_cubo_final": t == tam_max + 1}
                      for t, p in con.execute(sql_histograma).fetchall()]
        consultas.append(Consulta("histograma_tamano", sql_histograma.strip()))

        cond_k = " ".join(f"COUNT(*) FILTER (WHERE tamano <= {k}) * 1.0 / COUNT(*) AS pct_le_{k}," for k in k_candidatos)
        sql_pct_k = f"SELECT {cond_k[:-1]} FROM agg.agg_pedido"
        pct_k_valores = con.execute(sql_pct_k).fetchone()
        pct_por_k = dict(zip(k_candidatos, pct_k_valores))
        consultas.append(Consulta("pct_pedidos_por_umbral", sql_pct_k.strip()))

        sql_usuario_avg = f"""
        CREATE TEMP TABLE n9_usuario_avg AS
        SELECT user_id, AVG(tamano) AS tamano_medio_usuario
        FROM agg.agg_pedido GROUP BY user_id HAVING COUNT(*) >= {min_pedidos}
        """
        con.execute(sql_usuario_avg)
        consultas.append(Consulta("usuario_promedio", sql_usuario_avg.strip()))

        n_usuarios_intra = con.execute("SELECT COUNT(*) FROM n9_usuario_avg").fetchone()[0]

        def _factor(nombre_col_o_expr: str, nombre_consulta: str) -> list[dict]:
            sql = f"""
            SELECT {nombre_col_o_expr} AS valor,
                   COUNT(*) AS pedidos,
                   AVG(ap.tamano) AS tamano_medio_bruto,
                   AVG(ap.tamano - ua.tamano_medio_usuario) AS desviacion_intra_usuario
            FROM agg.agg_pedido ap
            JOIN n9_usuario_avg ua ON ua.user_id = ap.user_id
            GROUP BY 1 ORDER BY 1
            """
            consultas.append(Consulta(nombre_consulta, sql.strip()))
            return [
                {"valor": v, "pedidos": p, "tamano_medio_bruto": tb, "desviacion_intra_usuario": di}
                for v, p, tb, di in con.execute(sql).fetchall()
            ]

        factor_hora = _factor("ap.order_hour_of_day", "factor_hora")
        factor_dia = _factor("ap.order_dow", "factor_dia")
        factor_intervalo = _factor(_SQL_BUCKET_INTERVALO.replace("days_since_prior_order", "ap.days_since_prior_order"), "factor_intervalo")
        factor_k = _factor(_SQL_BUCKET_K.replace("k", "ap.k"), "factor_numero_pedido")

        con.execute("DROP TABLE n9_usuario_avg")
    finally:
        con.execute("DETACH agg")

    tabla_hist, _ = limitar_tabla(histograma)

    hallazgos = [
        f"Sobre {total_pedidos:,} pedidos: tamaño medio {media:.2f} productos, mediana {mediana:.0f}, "
        f"moda {moda:.0f}, P75={p75:.0f}, P90={p90:.0f}, P99={p99:.0f} (TAM_MAX medido).",
        "% de pedidos con tamaño <= k, en número de productos (sin montos): "
        + "; ".join(f"{k} productos: {pct_por_k[k]:.1%}" for k in k_candidatos) + ".",
        f"Desviación intra-usuario por factor (controla que un usuario que siempre pide grande no "
        f"se confunda con 'la hora hace pedidos grandes'), medida sobre {n_usuarios_intra:,} usuarios "
        f"con >= MIN_PEDIDOS={min_pedidos}: el rango de desviación intra-usuario por hora del día va de "
        f"{min(f['desviacion_intra_usuario'] for f in factor_hora):+.2f} a "
        f"{max(f['desviacion_intra_usuario'] for f in factor_hora):+.2f} productos; por intervalo desde "
        f"el pedido previo va de {min(f['desviacion_intra_usuario'] for f in factor_intervalo):+.2f} a "
        f"{max(f['desviacion_intra_usuario'] for f in factor_intervalo):+.2f}.",
    ]

    limitaciones = [
        "No hay precios: no se puede fijar un 'umbral de envío gratis' en dinero. La conclusión se "
        "expresa en número de productos y en % de pedidos que quedarían por debajo de ese número.",
        "El tamaño 'bruto' por factor mezcla composición de clientes (algunos usuarios siempre piden "
        "más, sin importar el factor): la desviación intra-usuario (tamaño del pedido menos el "
        "tamaño medio de ESE usuario) controla esa diferencia de composición: es la comparación más "
        "honesta de si el factor en sí se asocia con pedidos más grandes o chicos.",
        "Descriptivo, no causal: una asociación (p. ej. pedidos más grandes cuando el intervalo "
        "previo fue largo) no implica que alargar el intervalo cause pedidos más grandes.",
        f"El cubo 'sin_intervalo_previo' agrupa el primer pedido de cada usuario (days_since_prior_order "
        "NULL, no confundir con 0); '30_censurado' agrupa el valor topado del dataset (el intervalo "
        "real pudo ser mayor).",
    ]

    huella = huella_bd(con)
    duracion = time.perf_counter() - t_inicio

    return ResultadoNecesidad(
        id=ID,
        titulo=TITULO,
        pregunta=PREGUNTA,
        decision=DECISION,
        universo=Universo(
            texto="Pedidos con líneas (eval_set IN ('prior','train')).",
            filtros=["orders.eval_set IN ('prior','train')"],
        ),
        parametros=[
            Parametro("TAM_MAX", tam_max, f"Percentil 99 del tamaño de pedido ({p99:.0f}), redondeado; "
                                            "el histograma exacto llega hasta ahí y agrupa el resto en un cubo final."),
            Parametro("k_candidatos", list(k_candidatos), "Lista de umbrales candidatos (en número de "
                                                            "productos) para expresar '% de pedidos por debajo de'."),
            Parametro("MIN_PEDIDOS", min_pedidos, "Mínimo de pedidos con líneas por usuario para incluirlo "
                                                    "en el cálculo de desviación intra-usuario (su propio promedio "
                                                    "sería poco confiable con muy pocos pedidos)."),
        ],
        consultas=consultas,
        tablas={
            "histograma_tamano": tabla_hist,
            "factor_hora": factor_hora,
            "factor_dia": factor_dia,
            "factor_intervalo": factor_intervalo,
            "factor_numero_pedido": factor_k,
        },
        tablas_criterio_corte={
            "histograma_tamano": f"Tamaño exacto 1..{tam_max}, más un cubo >={tam_max+1}, orden tamano asc",
            "factor_hora": "24 horas (0-23), orden hora asc",
            "factor_dia": "7 días (0-6), orden día asc",
            "factor_intervalo": "5 cubos + NULL aparte, orden alfabético del cubo",
            "factor_numero_pedido": "6 cubos de order_number, orden del cubo",
        },
        valores_clave=[
            ValorClave("Tamaño medio", media, "productos", "estadisticos_globales"),
            ValorClave("Tamaño mediana", mediana, "productos", "estadisticos_globales"),
            ValorClave("TAM_MAX (P99)", tam_max, "productos", "estadisticos_globales"),
        ] + [
            ValorClave(f"% pedidos con tamaño <= {k}", pct_por_k[k], "fracción", "pct_pedidos_por_umbral")
            for k in k_candidatos
        ],
        hallazgos=hallazgos,
        limitaciones=limitaciones,
        duracion_s=duracion,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella),
    )
