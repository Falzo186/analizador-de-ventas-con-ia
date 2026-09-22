"""N11 — Predicción del siguiente pedido (baseline determinista, sin ML).

Objetivo: para los 131,209 usuarios cuyo pedido 'train' es la observación
objetivo, predecir sus productos usando SOLO sus pedidos 'prior'. Los
usuarios cuyo último pedido es 'test' no tienen respuesta conocida y no se
evalúan aquí (no hay ground truth).

score(u,p,DECAY) = Σ DECAY^d sobre los pedidos prior de u que contienen p,
dividido por Σ DECAY^d sobre TODOS los pedidos prior de u, con
d = (order_number del último prior de u) − (order_number del pedido).
DECAY=1.0 es frecuencia relativa pura.

Split sin fuga: user_id % 5 == 0 => VALIDACIÓN (20%, solo para elegir DECAY);
el resto (80%) es EVALUACIÓN, donde se reporta el resultado final. También
se reporta el mismo resultado sobre la validación, para exhibir el
optimismo de medir donde se eligió el parámetro.

Usa `agg_n11_score`/`agg_n11_usuarios` (precalculados, ver
`src.analytics.agregados`): construir el score para 131,209 usuarios sobre
sus ~13M líneas prior no cabe cómodamente en el presupuesto de 5s en vivo
(mismo orden de magnitud que el pipeline de N1).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.analytics.agregados import DECAYS_N11, abrir_agregado
from src.analytics.conexion import huella_bd, version_duckdb
from src.analytics.contrato import (
    Consulta,
    HuellaBD,
    Parametro,
    ResultadoNecesidad,
    Universo,
    ValorClave,
)

ID = "N11"
TITULO = "Predicción del siguiente pedido"
PREGUNTA = "¿Qué tan bien predice un baseline determinista (frecuencia+recencia, sin ML) los productos del siguiente pedido?"
DECISION = "Referencia de rendimiento alcanzable sin IA para comparar contra el LLM más adelante."

KS = (5, 10, 20)
K_SELECCION_DEFAULT = 10


def _col(decay: float) -> str:
    return f"score_d{int(decay * 10)}"


def _decay_label(decay: float) -> str:
    return str(int(decay * 10))


def _metricas_modelo(con: duckdb.DuckDBPyConnection, decay: float, k: int, filtro: str, target_tabla: str) -> dict:
    # Lee el ranking ya precalculado (agg_n11_rank, top-20 por usuario y
    # DECAY) en vez de recomputar ROW_NUMBER() sobre 8.47M filas en cada
    # llamada -- ver la nota de rendimiento en agregados._sql_construir_n11.
    # `target_tabla` es un temp table ya materializado (ver calcular()): así
    # el join contra 'train' no se recalcula en cada una de las 16 llamadas.
    dl = _decay_label(decay)
    sql = f"""
    WITH predichos AS (
        SELECT user_id, product_id FROM agg.agg_n11_rank
        WHERE decay_x10 = '{dl}' AND rango <= {k} AND {filtro}
    ),
    n_pred AS (SELECT user_id, COUNT(*) AS n FROM predichos GROUP BY user_id),
    target AS (SELECT user_id, product_id FROM {target_tabla}),
    n_target AS (SELECT user_id, COUNT(*) AS n FROM target GROUP BY user_id),
    hits AS (
        SELECT t.user_id, COUNT(*) AS n_hit
        FROM target t JOIN predichos p ON p.user_id = t.user_id AND p.product_id = t.product_id
        GROUP BY t.user_id
    )
    SELECT COUNT(DISTINCT nt.user_id),
           AVG(CASE WHEN COALESCE(h.n_hit, 0) >= 1 THEN 1.0 ELSE 0.0 END),
           AVG(COALESCE(h.n_hit, 0) * 1.0 / nt.n),
           AVG(COALESCE(h.n_hit, 0) * 1.0 / COALESCE(np.n, 1))
    FROM n_target nt
    LEFT JOIN hits h ON h.user_id = nt.user_id
    LEFT JOIN n_pred np ON np.user_id = nt.user_id
    """
    n_usuarios, hit_rate, recall, precision = con.execute(sql).fetchone()
    return {"n_usuarios": n_usuarios, "hit_rate": hit_rate, "recall_macro": recall,
            "precision_macro": precision, "sql": sql.strip()}


def _metricas_top_global(con: duckdb.DuckDBPyConnection, k: int, target_tabla: str) -> dict:
    sql = f"""
    WITH top_global AS (
        SELECT product_id FROM agg.agg_n1_producto ORDER BY lineas DESC, product_id ASC LIMIT {k}
    ),
    target AS (SELECT user_id, product_id FROM {target_tabla}),
    n_target AS (SELECT user_id, COUNT(*) AS n FROM target GROUP BY user_id),
    hits AS (
        SELECT t.user_id, COUNT(*) AS n_hit FROM target t
        WHERE t.product_id IN (SELECT product_id FROM top_global)
        GROUP BY t.user_id
    )
    SELECT COUNT(DISTINCT nt.user_id),
           AVG(CASE WHEN COALESCE(h.n_hit, 0) >= 1 THEN 1.0 ELSE 0.0 END),
           AVG(COALESCE(h.n_hit, 0) * 1.0 / nt.n),
           AVG(COALESCE(h.n_hit, 0) * 1.0 / {k})
    FROM n_target nt LEFT JOIN hits h ON h.user_id = nt.user_id
    """
    n_usuarios, hit_rate, recall, precision = con.execute(sql).fetchone()
    return {"n_usuarios": n_usuarios, "hit_rate": hit_rate, "recall_macro": recall,
            "precision_macro": precision, "sql": sql.strip()}


def _metricas_repetir_ultimo(con: duckdb.DuckDBPyConnection, k: int, filtro: str, target_tabla: str) -> dict:
    sql = f"""
    WITH ultimo_prior AS (
        SELECT o.user_id, o.order_id
        FROM orders o JOIN agg.agg_n11_usuarios u
            ON u.user_id = o.user_id AND u.ultimo_prior_k = o.order_number
        WHERE {filtro.replace('user_id', 'o.user_id')}
    ),
    rankeados AS (
        SELECT up.user_id, op.product_id,
               ROW_NUMBER() OVER (PARTITION BY up.user_id ORDER BY op.add_to_cart_order ASC) AS rango
        FROM ultimo_prior up JOIN order_products op ON op.order_id = up.order_id
    ),
    predichos AS (SELECT user_id, product_id FROM rankeados WHERE rango <= {k}),
    n_pred AS (SELECT user_id, COUNT(*) AS n FROM predichos GROUP BY user_id),
    target AS (SELECT user_id, product_id FROM {target_tabla}),
    n_target AS (SELECT user_id, COUNT(*) AS n FROM target GROUP BY user_id),
    hits AS (
        SELECT t.user_id, COUNT(*) AS n_hit
        FROM target t JOIN predichos p ON p.user_id = t.user_id AND p.product_id = t.product_id
        GROUP BY t.user_id
    )
    SELECT COUNT(DISTINCT nt.user_id),
           AVG(CASE WHEN COALESCE(h.n_hit, 0) >= 1 THEN 1.0 ELSE 0.0 END),
           AVG(COALESCE(h.n_hit, 0) * 1.0 / nt.n),
           AVG(COALESCE(h.n_hit, 0) * 1.0 / COALESCE(np.n, 1))
    FROM n_target nt
    LEFT JOIN hits h ON h.user_id = nt.user_id
    LEFT JOIN n_pred np ON np.user_id = nt.user_id
    """
    n_usuarios, hit_rate, recall, precision = con.execute(sql).fetchone()
    return {"n_usuarios": n_usuarios, "hit_rate": hit_rate, "recall_macro": recall,
            "precision_macro": precision, "sql": sql.strip()}


def predecir_usuario(
    con_agg: duckdb.DuckDBPyConnection, user_id: int, k: int, decay: float = 1.0
) -> list[dict]:
    """Interfaz reutilizable: top-k productos para `user_id` con su score y
    componentes (frecuencia = n_compras prior, recencia = order_number de la
    última compra prior de ese producto). Lee `agg_n11_score` directamente."""
    col = _col(decay)
    filas = con_agg.execute(
        f"""
        SELECT product_id, {col} AS score, n_compras, ultima_compra_k
        FROM agg_n11_score
        WHERE user_id = ?
        ORDER BY {col} DESC, ultima_compra_k DESC, product_id ASC
        LIMIT ?
        """,
        [user_id, k],
    ).fetchall()
    return [
        {
            "product_id": pid,
            "score": score,
            "componentes": {"frecuencia": n_compras, "recencia_ultima_compra_k": ultima_k},
        }
        for pid, score, n_compras, ultima_k in filas
    ]


def calcular(
    con: duckdb.DuckDBPyConnection,
    agg_path: Path,
    *,
    k_seleccion: int = K_SELECCION_DEFAULT,
) -> ResultadoNecesidad:
    t_inicio = time.perf_counter()
    consultas: list[Consulta] = []

    con_agg_check = abrir_agregado(agg_path, con)
    con_agg_check.close()
    con.execute(f"ATTACH '{Path(agg_path).as_posix()}' AS agg (READ_ONLY)")

    FILTRO_VAL = "user_id % 5 = 0"
    FILTRO_EVAL = "user_id % 5 != 0"

    try:
        sql_universo = "SELECT COUNT(*), COUNT(*) FILTER (WHERE user_id % 5 = 0) FROM agg.agg_n11_usuarios"
        n_usuarios_total, n_val = con.execute(sql_universo).fetchone()
        consultas.append(Consulta("universo_usuarios", sql_universo))
        n_eval = n_usuarios_total - n_val

        sql_techo = """
        WITH target AS (
            SELECT o.user_id, op.product_id
            FROM orders o JOIN order_products op ON op.order_id = o.order_id
            WHERE o.eval_set = 'train'
        )
        SELECT COUNT(*), COUNT(s.product_id)
        FROM target t LEFT JOIN agg.agg_n11_score s ON s.user_id = t.user_id AND s.product_id = t.product_id
        """
        total_lineas_objetivo, lineas_alcanzables = con.execute(sql_techo).fetchone()
        consultas.append(Consulta("techo_alcanzable", sql_techo.strip()))

        # Materializa una sola vez las líneas objetivo por split (evaluación/
        # validación): evita recalcular el join a order_products 16 veces
        # (una por cada llamada a las funciones de métricas de abajo).
        con.execute(f"""
        CREATE TEMP TABLE n11_target_eval AS
        SELECT o.user_id, op.product_id FROM orders o JOIN order_products op ON op.order_id = o.order_id
        WHERE o.eval_set = 'train' AND {FILTRO_EVAL}
        """)
        con.execute(f"""
        CREATE TEMP TABLE n11_target_val AS
        SELECT o.user_id, op.product_id FROM orders o JOIN order_products op ON op.order_id = o.order_id
        WHERE o.eval_set = 'train' AND {FILTRO_VAL}
        """)

        # Selección de DECAY SOLO con el conjunto de validación (K_SELECCION fijo).
        # Si el universo de validación queda vacío (posible en universos muy
        # chicos, p. ej. tests), hit_rate es NULL para todos los candidatos:
        # no hay base para elegir y se usa DECAY=1.0 (frecuencia pura) por
        # default, documentado como tal en vez de comparar None entre sí.
        seleccion = []
        for d in DECAYS_N11:
            m = _metricas_modelo(con, d, k_seleccion, FILTRO_VAL, "n11_target_val")
            seleccion.append((d, m["hit_rate"]))
        candidatos_validos = [(d, hr) for d, hr in seleccion if hr is not None]
        decay_elegido = max(candidatos_validos, key=lambda x: x[1])[0] if candidatos_validos else DECAYS_N11[0]
        consultas.append(Consulta(
            "seleccion_decay_en_validacion",
            f"-- hit_rate@{k_seleccion} en VALIDACIÓN (user_id % 5 = 0) por DECAY: {seleccion}. "
            f"Elegido: DECAY={decay_elegido} (nunca se miró el conjunto de evaluación para esto).",
        ))

        tabla_metricas = []
        for k in KS:
            m_eval = _metricas_modelo(con, decay_elegido, k, FILTRO_EVAL, "n11_target_eval")
            m_val = _metricas_modelo(con, decay_elegido, k, FILTRO_VAL, "n11_target_val")
            m_pop = _metricas_top_global(con, k, "n11_target_eval")
            m_rep = _metricas_repetir_ultimo(con, k, FILTRO_EVAL, "n11_target_eval")
            consultas.append(Consulta(f"metricas_modelo_eval_k{k}", m_eval["sql"]))
            consultas.append(Consulta(f"metricas_modelo_val_k{k}", m_val["sql"]))
            consultas.append(Consulta(f"metricas_top_global_k{k}", m_pop["sql"]))
            consultas.append(Consulta(f"metricas_repetir_ultimo_k{k}", m_rep["sql"]))
            for nombre, m in (("baseline_decay_recencia", m_eval),
                              ("baseline_decay_recencia_EN_VALIDACION", m_val),
                              ("top_k_populares_globales", m_pop),
                              ("repetir_ultimo_pedido_prior", m_rep)):
                tabla_metricas.append({
                    "metodo": nombre, "k": k, "n_usuarios": m["n_usuarios"],
                    "hit_rate": m["hit_rate"], "recall_macro": m["recall_macro"],
                    "precision_macro": m["precision_macro"],
                })
    finally:
        con.execute("DROP TABLE IF EXISTS n11_target_eval")
        con.execute("DROP TABLE IF EXISTS n11_target_val")
        con.execute("DETACH agg")

    pct_alcanzable = lineas_alcanzables / total_lineas_objetivo
    fila_eval_k10 = next(f for f in tabla_metricas if f["metodo"] == "baseline_decay_recencia" and f["k"] == 10)
    fila_pop_k10 = next(f for f in tabla_metricas if f["metodo"] == "top_k_populares_globales" and f["k"] == 10)
    fila_rep_k10 = next(f for f in tabla_metricas if f["metodo"] == "repetir_ultimo_pedido_prior" and f["k"] == 10)
    supera_baselines = (
        fila_eval_k10["hit_rate"] >= fila_pop_k10["hit_rate"]
        and fila_eval_k10["hit_rate"] >= fila_rep_k10["hit_rate"]
    )

    hallazgos = [
        f"Universo: {n_usuarios_total:,} usuarios con pedido 'train' (131,209 esperado). Split sin "
        f"fuga: {n_val:,} en validación (user_id%5=0, usados solo para elegir DECAY) y {n_eval:,} en "
        f"evaluación. Se eligió DECAY={decay_elegido} por hit_rate@{k_seleccion} en validación "
        f"({dict(seleccion)}), sin mirar el conjunto de evaluación.",
        f"TECHO: {lineas_alcanzables:,} de {total_lineas_objetivo:,} líneas objetivo "
        f"({pct_alcanzable:.1%}) corresponden a un producto que el usuario ya había comprado en "
        "prior; el resto son productos nuevos, inalcanzables para cualquier baseline basado solo "
        "en historial.",
        f"En evaluación (K=10): hit_rate={fila_eval_k10['hit_rate']:.1%}, "
        f"recall@10={fila_eval_k10['recall_macro']:.1%}, precision@10={fila_eval_k10['precision_macro']:.1%}. "
        f"Comparado con los baselines de contexto (K=10): top-K populares hit_rate="
        f"{fila_pop_k10['hit_rate']:.1%}; repetir último pedido prior hit_rate={fila_rep_k10['hit_rate']:.1%}. "
        + ("El baseline principal SÍ supera a ambos baselines de contexto en K=10."
           if supera_baselines else
           "El baseline principal NO supera a ambos baselines de contexto en K=10 — se reporta sin maquillar."),
        f"La métrica en VALIDACIÓN (donde se eligió DECAY) tiende a verse igual o mejor que en "
        "EVALUACIÓN por el propio proceso de selección; comparar ambas filas en la tabla "
        "muestra ese optimismo, aunque en este baseline determinista (sin parámetros ajustados "
        "por gradiente) el efecto es pequeño.",
    ]

    limitaciones = [
        "Baseline sin ML: es una fórmula determinista de frecuencia y recencia, no un modelo "
        "entrenado. Sirve como referencia de piso, no como el mejor predictor posible.",
        "Los usuarios con último pedido 'test' (75,000) no tienen respuesta conocida (Instacart no "
        "publicó esos pedidos) y no se evalúan aquí.",
        "El TECHO es una propiedad del dataset, no del método: ningún baseline basado solo en "
        "historial de compras puede acertar un producto que el usuario nunca compró antes.",
        "DECAY se eligió únicamente con el 20% de validación (hit_rate@10); K=5/10/20 se reportan "
        "los tres, no se optimiza un único K.",
        "No hay precios: el ranking y las métricas son de aciertos de producto, no de valor de canasta.",
    ]

    huella = huella_bd(con)
    duracion = time.perf_counter() - t_inicio

    return ResultadoNecesidad(
        id=ID,
        titulo=TITULO,
        pregunta=PREGUNTA,
        decision=DECISION,
        universo=Universo(
            texto="Usuarios cuyo pedido más reciente tiene eval_set='train' (131,209); se predice "
                  "usando solo sus pedidos 'prior'.",
            filtros=["orders.eval_set = 'train' (objetivo)", "orders.eval_set = 'prior' (historial)"],
        ),
        parametros=[
            Parametro("DECAY candidatos", list(DECAYS_N11), "1.0 (frecuencia pura), 0.9, 0.8, 0.7: "
                                                               "rango convencional de decaimiento exponencial suave a fuerte."),
            Parametro("DECAY elegido", decay_elegido, f"Elegido por hit_rate@{k_seleccion} en el 20% "
                                                          f"de validación (user_id%5=0): {dict(seleccion)}."),
            Parametro("K evaluados", list(KS), "Los tres valores de K pedidos por el brief; no se elige uno solo."),
        ],
        consultas=consultas,
        tablas={"metricas": tabla_metricas},
        tablas_criterio_corte={
            "metricas": "4 métodos x 3 valores de K = 12 filas (todas), sin truncar",
        },
        valores_clave=[
            ValorClave("Usuarios evaluados", n_usuarios_total, "usuarios", "universo_usuarios"),
            ValorClave("DECAY elegido", decay_elegido, "decay", "seleccion_decay_en_validacion"),
            ValorClave("Techo alcanzable", pct_alcanzable, "fracción", "techo_alcanzable"),
            ValorClave("hit_rate@10 (evaluación)", fila_eval_k10["hit_rate"], "fracción", "metricas_modelo_eval_k10"),
            ValorClave("Supera a ambos baselines en K=10", supera_baselines, "booleano", "metricas_modelo_eval_k10"),
        ],
        hallazgos=hallazgos,
        limitaciones=limitaciones,
        duracion_s=duracion,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella),
    )
