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

    # C1 (Fase 2B): `típico` es la mediana de los intervalos PREVIOS al
    # último pedido del usuario (excluye explícitamente el intervalo del
    # order_number máximo, que es justo el que se compara como `último`).
    # Antes se incluía ese mismo valor dentro de la mediana, lo que sesga
    # "típico" hacia "último" y diluye la señal de anomalía. MIN_INTERVALOS
    # también se cuenta sobre ese conjunto reducido (previos), no sobre
    # todos los intervalos del usuario.
    sql_elegibles = f"""
    CREATE TEMP TABLE n4_elegibles AS
    WITH max_on AS (
        SELECT user_id, MAX(order_number) AS max_on FROM orders GROUP BY user_id
    ),
    ultimo AS (
        SELECT o.user_id, o.days_since_prior_order AS ultimo
        FROM orders o JOIN max_on m ON m.user_id = o.user_id AND m.max_on = o.order_number
    ),
    previos AS (
        SELECT o.user_id, o.days_since_prior_order AS dias
        FROM orders o
        JOIN max_on m ON m.user_id = o.user_id
        WHERE o.days_since_prior_order IS NOT NULL AND o.order_number <> m.max_on
    ),
    tipico AS (
        SELECT user_id, MEDIAN(dias) AS tipico, COUNT(*) AS n_intervalos
        FROM previos GROUP BY user_id
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

    # C5: invariante evaluables + excluidos = total_usuarios, con una
    # consulta INDEPENDIENTE de n4_elegibles (recalcula desde orders, no
    # reutiliza la tabla temporal de arriba).
    sql_excluidos = f"""
    WITH max_on AS (
        SELECT user_id, MAX(order_number) AS max_on FROM orders GROUP BY user_id
    ),
    n_previos AS (
        SELECT o.user_id, COUNT(*) AS n
        FROM orders o
        JOIN max_on m ON m.user_id = o.user_id
        WHERE o.days_since_prior_order IS NOT NULL AND o.order_number <> m.max_on
        GROUP BY o.user_id
    )
    SELECT COUNT(*) FROM (SELECT DISTINCT user_id FROM orders) u
    LEFT JOIN n_previos np ON np.user_id = u.user_id
    WHERE COALESCE(np.n, 0) < {min_intervalos}
    """
    n_excluidos = con.execute(sql_excluidos).fetchone()[0]
    consultas.append(Consulta("excluidos_por_min_intervalos", sql_excluidos.strip()))

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

    # C5: refuerzo del invariante evaluables + excluidos = total_usuarios,
    # con las dos consultas anteriores (independientes entre sí: una monta
    # n4_elegibles, la otra recalcula desde cero con su propia CTE).
    assert n_elegibles + n_excluidos == total_usuarios, (
        f"invariante roto: {n_elegibles} elegibles + {n_excluidos} excluidos "
        f"!= {total_usuarios} usuarios totales"
    )

    pct_elegibles = n_elegibles / total_usuarios if total_usuarios else 0.0
    pct_en_riesgo = n_en_riesgo / n_elegibles if n_elegibles else 0.0
    pct_censurado_en_riesgo = n_en_riesgo_censurado / n_en_riesgo if n_en_riesgo else 0.0
    pct_sensibles = n_sensibles / n_elegibles if n_elegibles else 0.0

    hallazgos = [
        f"De {total_usuarios:,} usuarios, {n_elegibles:,} ({pct_elegibles:.1%}) tienen "
        f"al menos MIN_INTERVALOS={min_intervalos} intervalos PREVIOS a su último pedido (el intervalo "
        f"del último pedido se excluye de 'típico' a propósito, ver limitaciones) y se pueden evaluar; "
        f"los {n_excluidos:,} restantes no alcanzan ese mínimo."
        + (f" La razón último/típico entre los evaluables tiene mediana {mediana_razon:.2f}, "
           f"P75={p75_razon:.2f}, P90={p90_razon:.2f}." if n_elegibles else ""),
        f"{n_en_riesgo:,} usuarios ({pct_en_riesgo:.1%} de los evaluables) están 'en riesgo' "
        f"(razón >= {umbral_razon} y diferencia >= {umbral_dias} días frente a su propio patrón).",
        f"De esos {n_en_riesgo:,} usuarios en riesgo, {n_en_riesgo_censurado:,} "
        f"({pct_censurado_en_riesgo:.1%}) tienen su último intervalo topado en 30 (censurado): "
        "su intervalo real podría ser mayor a 30 días, el dato no lo distingue.",
        f"{n_sensibles:,} usuarios ({pct_sensibles:.1%} de los evaluables) se marcan 'en riesgo' "
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
        "'típico' se calcula SOLO con los intervalos previos al último pedido del usuario: el "
        "intervalo del último pedido nunca entra en su propia mediana de referencia (si entrara, "
        "un 'último' atípico arrastraría la mediana hacia sí mismo, diluyendo la señal de anomalía "
        "que se quiere detectar).",
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
                      "Medido sobre la distribución de intervalos PREVIOS al último pedido de cada "
                      "usuario (universo completo de orders, sin filtrar eval_set; ya excluye el "
                      "intervalo del último pedido, ver corrección C1 en docs/FASE2B_REPORTE.md): "
                      "88.4% de los usuarios tienen >=3 previos, 71.0% tienen >=5, 45.5% tienen >=10. "
                      "Se eligió 5 (default sugerido en el brief) porque retiene a la mayoría de los "
                      "usuarios (71.0%) con una mediana ya razonablemente estable."),
            Parametro("UMBRAL_RAZON", umbral_razon,
                      ("Medido sobre la distribución de razón entre elegibles: "
                       f"mediana={mediana_razon:.2f}, P75={p75_razon:.2f}, P90={p90_razon:.2f}. "
                       "UMBRAL_RAZON=2.0 cae entre P75 y P90, un corte selectivo pero no extremo."
                       if n_elegibles else "Sin productos elegibles en este universo para medir la distribución.")),
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
            ValorClave("Usuarios excluidos por MIN_INTERVALOS", n_excluidos, "usuarios", "excluidos_por_min_intervalos"),
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
