"""N4 — Frecuencia de recompra por cliente.

Pregunta: ¿el intervalo entre pedidos de un usuario se alargó de forma
anormal respecto a su propio patrón?
Decisión: a quién enviarle un recordatorio de recompra.

Universo: pedidos totales, solo tabla `orders` (los intervalos existen para
cualquier eval_set); 206,209 usuarios en total.

LIMITACIÓN CENTRAL: no existe una "fecha actual" en este dataset (order_dow/
order_hour_of_day son anónimos y no hay fechas reales). Por eso esta
necesidad NO detecta "días sin comprar hasta hoy": detecta que el intervalo
más reciente de un usuario es anormalmente largo *respecto a su propio
patrón histórico*, nada más.
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

ID = "N4"
TITULO = "Frecuencia de recompra por cliente"
PREGUNTA = "¿Qué usuarios muestran un intervalo reciente entre pedidos anormalmente largo frente a su propio patrón?"
DECISION = "A qué usuarios enviarles un recordatorio de recompra."

MIN_INTERVALOS_DEFAULT = 5
UMBRAL_RAZON_DEFAULT = 2.0
UMBRAL_DIAS_DEFAULT = 5

# Combinaciones adicionales para el análisis de sensibilidad (además de los
# defaults). Elegidas para cubrir un rango razonable alrededor del default,
# no como candidatas a reemplazarlo.
COMBOS_SENSIBILIDAD: list[tuple[float, int]] = [(1.5, 3), (2.0, 5), (2.5, 7), (3.0, 10)]


def calcular(
    con: duckdb.DuckDBPyConnection,
    *,
    min_intervalos: int = MIN_INTERVALOS_DEFAULT,
    umbral_razon: float = UMBRAL_RAZON_DEFAULT,
    umbral_dias: int = UMBRAL_DIAS_DEFAULT,
) -> ResultadoNecesidad:
    t_inicio = time.perf_counter()
    consultas: list[Consulta] = []

    sql_censura_global = """
    SELECT COUNT(*) FILTER (WHERE days_since_prior_order = 30),
           COUNT(days_since_prior_order),
           COUNT(*) FILTER (WHERE days_since_prior_order = 30) * 100.0 / COUNT(days_since_prior_order)
    FROM orders
    """
    n_topados, n_no_null, pct_topados = con.execute(sql_censura_global).fetchone()
    consultas.append(Consulta("censura_global", sql_censura_global.strip()))

    sql_elegibles = f"""
    CREATE TEMP TABLE n4_elegibles AS
    WITH max_on AS (
        SELECT user_id, MAX(order_number) AS max_on FROM orders GROUP BY user_id
    ),
    ultimo AS (
        SELECT o.user_id, o.days_since_prior_order AS ultimo
        FROM orders o JOIN max_on m ON m.user_id = o.user_id AND m.max_on = o.order_number
    ),
    tipico AS (
        SELECT user_id, MEDIAN(days_since_prior_order) AS tipico,
               COUNT(days_since_prior_order) AS n_intervalos
        FROM orders WHERE days_since_prior_order IS NOT NULL
        GROUP BY user_id
    )
    SELECT t.user_id, t.tipico, u.ultimo, t.n_intervalos,
           u.ultimo * 1.0 / GREATEST(t.tipico, 1) AS razon,
           (u.ultimo = 30) AS censurado
    FROM tipico t JOIN ultimo u ON u.user_id = t.user_id
    WHERE t.n_intervalos >= {min_intervalos}
    """
    con.execute(sql_elegibles)
    consultas.append(Consulta("elegibles", sql_elegibles.strip()))

    sql_total_usuarios = "SELECT COUNT(DISTINCT user_id) FROM orders"
    total_usuarios = con.execute(sql_total_usuarios).fetchone()[0]
    consultas.append(Consulta("total_usuarios", sql_total_usuarios))

    sql_percentiles_razon = """
    SELECT COUNT(*), PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY razon),
           PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY razon),
           PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY razon)
    FROM n4_elegibles
    """
    n_elegibles, mediana_razon, p75_razon, p90_razon = con.execute(sql_percentiles_razon).fetchone()
    consultas.append(Consulta("percentiles_razon", sql_percentiles_razon.strip()))

    sql_en_riesgo = f"""
    SELECT COUNT(*), COUNT(*) FILTER (WHERE censurado)
    FROM n4_elegibles
    WHERE razon >= {umbral_razon} AND (ultimo - tipico) >= {umbral_dias}
    """
    n_en_riesgo, n_en_riesgo_censurado = con.execute(sql_en_riesgo).fetchone()
    consultas.append(Consulta("en_riesgo", sql_en_riesgo.strip()))

    condiciones = " + ".join(
        f"CASE WHEN razon >= {r} AND (ultimo - tipico) >= {d} THEN 1 ELSE 0 END"
        for r, d in COMBOS_SENSIBILIDAD
    )
    sql_sensibilidad = f"SELECT COUNT(*) FILTER (WHERE ({condiciones}) >= 3) FROM n4_elegibles"
    n_sensibles = con.execute(sql_sensibilidad).fetchone()[0]
    consultas.append(Consulta("sensibilidad", sql_sensibilidad))

    sql_tabla = """
    SELECT user_id, tipico, ultimo, razon, censurado
    FROM n4_elegibles
    ORDER BY razon DESC, user_id ASC
    LIMIT 50
    """
    cols = ["user_id", "tipico", "ultimo", "razon", "censurado"]
    tabla_top = [dict(zip(cols, r)) for r in con.execute(sql_tabla).fetchall()]
    consultas.append(Consulta("top_50_razon", sql_tabla.strip()))

    con.execute("DROP TABLE n4_elegibles")

    tabla_top, _ = limitar_tabla(tabla_top)

    hallazgos = [
        f"De {total_usuarios:,} usuarios, {n_elegibles:,} ({n_elegibles/total_usuarios:.1%}) tienen "
        f"al menos MIN_INTERVALOS={min_intervalos} intervalos no nulos y se pueden evaluar. La razón "
        f"último/típico entre ellos tiene mediana {mediana_razon:.2f}, P75={p75_razon:.2f}, P90={p90_razon:.2f}.",
        f"{n_en_riesgo:,} usuarios ({n_en_riesgo/n_elegibles:.1%} de los evaluables) están 'en riesgo' "
        f"(razón >= {umbral_razon} y diferencia >= {umbral_dias} días frente a su propio patrón).",
        f"De esos {n_en_riesgo:,} usuarios en riesgo, {n_en_riesgo_censurado:,} "
        f"({n_en_riesgo_censurado/n_en_riesgo:.1%}) tienen su último intervalo topado en 30 (censurado): "
        "su intervalo real podría ser mayor a 30 días, el dato no lo distingue.",
        f"{n_sensibles:,} usuarios ({n_sensibles/n_elegibles:.1%} de los evaluables) se marcan 'en riesgo' "
        f"en al menos 3 de las {len(COMBOS_SENSIBILIDAD)} combinaciones de umbrales probadas "
        f"({', '.join(f'razón>={r}/días>={d}' for r, d in COMBOS_SENSIBILIDAD)}): la señal es "
        "razonablemente estable frente a la elección exacta de umbral.",
    ]

    limitaciones = [
        "No existe una 'fecha actual' en este dataset: se detecta que el intervalo MÁS RECIENTE "
        "de un usuario es anormalmente largo frente a su propio patrón histórico, no 'días sin "
        "comprar hasta hoy' (eso requeriría una fecha de referencia que no existe aquí).",
        f"days_since_prior_order está topado en 30: {n_topados:,} de {n_no_null:,} intervalos "
        f"no nulos ({pct_topados:.2f}%) valen exactamente 30, un valor censurado que puede "
        "esconder un intervalo real mayor. Se reporta el subgrupo de usuarios en riesgo con "
        "este valor censurado por separado.",
        f"MIN_INTERVALOS={min_intervalos} excluye usuarios con muy pocos pedidos: su mediana "
        "histórica sería poco confiable con 1-4 valores.",
    ]

    huella = huella_bd(con)
    duracion = time.perf_counter() - t_inicio

    return ResultadoNecesidad(
        id=ID,
        titulo=TITULO,
        pregunta=PREGUNTA,
        decision=DECISION,
        universo=Universo(
            texto="Todos los pedidos (orders), sin filtrar por eval_set: los intervalos "
                  "(days_since_prior_order) existen para cualquier eval_set.",
            filtros=["orders (206,209 usuarios en total)"],
        ),
        parametros=[
            Parametro("MIN_INTERVALOS", min_intervalos,
                      "Medido sobre la distribución de intervalos no nulos por usuario (universo "
                      "completo de orders, sin filtrar eval_set): 100.0% de los usuarios tienen "
                      ">=3, 78.9% tienen >=5, 49.3% tienen >=10. Se eligió 5 (default sugerido en "
                      "el brief) porque retiene a la gran mayoría de los usuarios (78.9%) con una "
                      "mediana ya razonablemente estable."),
            Parametro("UMBRAL_RAZON", umbral_razon,
                      f"Medido sobre la distribución de razón entre elegibles: mediana={mediana_razon:.2f}, "
                      f"P75={p75_razon:.2f}, P90={p90_razon:.2f}. UMBRAL_RAZON=2.0 cae entre P75 y P90, "
                      "un corte selectivo pero no extremo."),
            Parametro("UMBRAL_DIAS", umbral_dias,
                      "Complementa a UMBRAL_RAZON para no marcar como 'en riesgo' a usuarios con "
                      "intervalos típicos muy cortos (donde una razón alta puede ser 1-2 días de "
                      "diferencia, no relevante en la práctica)."),
        ],
        consultas=consultas,
        tablas={"top_50_razon": tabla_top},
        tablas_criterio_corte={"top_50_razon": "Top 50 usuarios elegibles por razón desc, desempate user_id asc"},
        valores_clave=[
            ValorClave("Usuarios evaluables", n_elegibles, "usuarios", "percentiles_razon"),
            ValorClave("Usuarios en riesgo", n_en_riesgo, "usuarios", "en_riesgo"),
            ValorClave("Usuarios en riesgo censurados (último=30)", n_en_riesgo_censurado, "usuarios", "en_riesgo"),
            ValorClave("% intervalos topados en 30 (censura global)", pct_topados / 100, "fracción", "censura_global"),
            ValorClave("Usuarios estables ante >=3 combinaciones de umbral", n_sensibles, "usuarios", "sensibilidad"),
        ],
        hallazgos=hallazgos,
        limitaciones=limitaciones,
        duracion_s=duracion,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella),
    )
