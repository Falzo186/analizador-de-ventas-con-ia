"""N3 — Picos de demanda: pedidos por día x hora.

Pregunta: ¿cuándo hay más y menos pedidos, en términos relativos?
Decisión: ventanas candidatas para reforzar personal/repartidores o para
promociones (en los valles) — como índice relativo, nunca como dotación
absoluta (no hay datos de capacidad en este dataset).

Universo: pedidos TOTALES (orders.eval_set puede ser prior/train/test: aquí
se cuentan pedidos, no productos, así que el pedido 'test' sí cuenta).

`order_dow` (0-6) y `order_hour_of_day` (0-23) son anónimos: no se afirma a
qué día real corresponde el 0.
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
    limitar_tabla,
)

ID = "N3"
TITULO = "Picos de demanda"
PREGUNTA = "¿En qué combinaciones de día y hora hay más y menos pedidos, en términos relativos?"
DECISION = "Ventanas candidatas para reforzar personal/repartidores (picos) o para promociones (valles). Índice relativo, no dotación absoluta."

PERCENTIL_PICO_DEFAULT = 0.9
PERCENTIL_VALLE_DEFAULT = 0.1


def calcular(
    con: duckdb.DuckDBPyConnection,
    *,
    percentil_pico: float = PERCENTIL_PICO_DEFAULT,
    percentil_valle: float = PERCENTIL_VALLE_DEFAULT,
) -> ResultadoNecesidad:
    t_inicio = time.perf_counter()
    consultas: list[Consulta] = []

    sql_matriz = """
    SELECT order_dow, order_hour_of_day, COUNT(*) AS n_pedidos
    FROM orders
    GROUP BY order_dow, order_hour_of_day
    ORDER BY order_dow, order_hour_of_day
    """
    celdas = con.execute(sql_matriz).fetchall()
    consultas.append(Consulta("matriz_dow_hora", sql_matriz.strip()))

    sql_marginal_dia = "SELECT order_dow, COUNT(*) AS n_pedidos FROM orders GROUP BY order_dow ORDER BY order_dow"
    marginal_dia = con.execute(sql_marginal_dia).fetchall()
    consultas.append(Consulta("marginal_dia", sql_marginal_dia))

    sql_marginal_hora = "SELECT order_hour_of_day, COUNT(*) AS n_pedidos FROM orders GROUP BY order_hour_of_day ORDER BY order_hour_of_day"
    marginal_hora = con.execute(sql_marginal_hora).fetchall()
    consultas.append(Consulta("marginal_hora", sql_marginal_hora))

    sql_percentiles = f"""
    WITH celdas AS (
        SELECT order_dow, order_hour_of_day, COUNT(*) AS n_pedidos
        FROM orders GROUP BY order_dow, order_hour_of_day
    )
    SELECT AVG(n_pedidos),
           PERCENTILE_CONT({percentil_pico}) WITHIN GROUP (ORDER BY n_pedidos),
           PERCENTILE_CONT({percentil_valle}) WITHIN GROUP (ORDER BY n_pedidos),
           COUNT(*)
    FROM celdas
    """
    promedio_celda, umbral_pico, umbral_valle, n_celdas = con.execute(sql_percentiles).fetchone()
    consultas.append(Consulta("percentiles_celda", sql_percentiles.strip()))

    total_pedidos = sum(n for _, _, n in celdas)

    filas_matriz = [
        {
            "order_dow": dow, "order_hour_of_day": hora, "n_pedidos": n,
            "indice": n / promedio_celda,
            "pico": n >= umbral_pico,
            "valle": n <= umbral_valle,
        }
        for dow, hora, n in celdas
    ]
    filas_matriz.sort(key=lambda r: (-r["n_pedidos"], r["order_dow"], r["order_hour_of_day"]))

    picos = [f for f in filas_matriz if f["pico"]]
    valles = [f for f in filas_matriz if f["valle"]]
    avg_pico = sum(f["n_pedidos"] for f in picos) / len(picos)
    avg_valle = sum(f["n_pedidos"] for f in valles) / len(valles)
    razon_pico_valle = avg_pico / avg_valle

    celda_max = max(filas_matriz, key=lambda r: r["n_pedidos"])

    # Ventana de 4 horas consecutivas más cargada, sobre el marginal por hora
    # (sin envolver medianoche: horas 0..23, ventanas [h, h+3] con h en 0..20).
    marginal_hora_dict = dict(marginal_hora)
    mejor_inicio, mejor_suma = 0, -1
    for h in range(0, 21):
        s = sum(marginal_hora_dict.get(h + i, 0) for i in range(4))
        if s > mejor_suma:
            mejor_suma, mejor_inicio = s, h
    pct_ventana = mejor_suma / total_pedidos

    tabla_matriz, _ = limitar_tabla(filas_matriz)

    hallazgos = [
        f"La celda con más pedidos es (Día {celda_max['order_dow']}, Hora {celda_max['order_hour_of_day']}) "
        f"con {celda_max['n_pedidos']:,} pedidos (índice={celda_max['indice']:.2f}x el promedio de celda).",
        f"{len(picos)} de {n_celdas} celdas están en el percentil {int(percentil_pico*100)} o más "
        f"(pico) y {len(valles)} en el percentil {int(percentil_valle*100)} o menos (valle); la razón "
        f"pico/valle (promedio de pedidos en celdas pico vs. celdas valle) es {razon_pico_valle:.2f}x.",
        f"La ventana de 4 horas consecutivas más cargada es Hora {mejor_inicio} a Hora {mejor_inicio+3} "
        f"({mejor_suma:,} pedidos, {pct_ventana:.1%} de los {total_pedidos:,} pedidos totales).",
    ]

    limitaciones = [
        "order_dow (0-6) y order_hour_of_day (0-23) son anónimos: no se sabe a qué día real "
        "corresponde el 0, ni la zona horaria. Las etiquetas se muestran como 'Día 0..6'.",
        "No hay datos de capacidad (personal, repartidores disponibles, tiempos de entrega): "
        "esta necesidad NO calcula dotación absoluta, solo un índice relativo de demanda "
        "(celda / promedio de celdas) y ventanas candidatas.",
        "Se cuentan pedidos totales (orders), incluidos los 75,000 con eval_set='test': tienen "
        "día/hora válidos aunque no tengan productos, y para 'cuándo llegan pedidos' sí cuentan.",
    ]

    huella = huella_bd(con)
    duracion = time.perf_counter() - t_inicio

    return ResultadoNecesidad(
        id=ID,
        titulo=TITULO,
        pregunta=PREGUNTA,
        decision=DECISION,
        universo=Universo(
            texto="Todos los pedidos (orders), sin filtrar por eval_set: se cuentan pedidos, no productos.",
            filtros=["orders (pedidos totales, incluye eval_set='test')"],
        ),
        parametros=[
            Parametro("percentil_pico", percentil_pico,
                      "P90 es el corte convencional para 'atípicamente alto'; aplicado sobre las "
                      "168 celdas día×hora."),
            Parametro("percentil_valle", percentil_valle,
                      "P10 es el corte convencional para 'atípicamente bajo', simétrico a percentil_pico."),
        ],
        consultas=consultas,
        tablas={"matriz_dow_hora": tabla_matriz},
        tablas_criterio_corte={
            "matriz_dow_hora": "Las 168 celdas día×hora (7x24), orden n_pedidos desc, desempate order_dow/order_hour_of_day asc",
        },
        valores_clave=[
            ValorClave("Día pico", celda_max["order_dow"], "día (0-6)", "matriz_dow_hora"),
            ValorClave("Hora pico", celda_max["order_hour_of_day"], "hora (0-23)", "matriz_dow_hora"),
            ValorClave("Razón pico/valle", razon_pico_valle, "razón", "percentiles_celda"),
            ValorClave("% pedidos en la ventana de 4h más cargada", pct_ventana, "fracción", "marginal_hora"),
        ],
        hallazgos=hallazgos,
        limitaciones=limitaciones,
        duracion_s=duracion,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella),
    )
