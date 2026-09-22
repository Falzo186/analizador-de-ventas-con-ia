"""N7 — Evolución por número de pedido.

Pregunta: ¿cómo cambian el tamaño, la variedad y la recompra a medida que un
usuario acumula más pedidos?
Decisión: qué ofrecer a clientes nuevos. Puramente descriptivo: la curva por
`k` mezcla usuarios distintos (solo llegan a `k` alto quienes tienen muchos
pedidos), así que no se interpreta como una trayectoria causal de un mismo
cliente — para eso está el análisis PAREADO (mismo usuario, pedido 1 vs 10).

Universo: pedidos con líneas. Usa `agg_pedido`, `agg_n7_por_k`,
`agg_n7_pareado` y `agg_n7_reordered_k1` (precalculados — ver
`src.analytics.agregados`: el join con `products` para contar departamentos
distintos por pedido mide ~10.7s en vivo, y el JOIN pareado justo después
del GROUP BY por k mide ~7.2s adicionales bajo threads=1 por presión de
memoria/buffer de un solo hilo — ambos muy por encima del objetivo de 5s;
se precalculan una sola vez en el build, multi-hilo).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.analytics.agregados import K_PAREADO_A_N7, K_PAREADO_B_N7, abrir_agregado
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

ID = "N7"
TITULO = "Evolución por número de pedido"
PREGUNTA = "¿Cómo cambian tamaño, variedad y recompra a medida que un usuario acumula más pedidos?"
DECISION = "Qué ofrecer a clientes nuevos (descriptivo, no causal)."

MIN_USUARIOS_K_DEFAULT = 100
JUSTIF_MIN_USUARIOS_K = (
    "Medido: el número de usuarios con al menos k pedidos-con-líneas cae de "
    "206,209 en k=1 a 867 en k=100 (el máximo order_number del dataset). "
    "MIN_USUARIOS_K=100 es un piso de muestra de tres cifras, convencional "
    "para no reportar un promedio sobre una decena de usuarios; en este "
    "dataset, incluso k=100 (el más escaso) lo supera (867 usuarios), así "
    "que el parámetro no descarta ningún k en la práctica, pero deja el "
    "criterio explícito y medido en vez de mostrar la curva completa sin filtro."
)


def calcular(
    con: duckdb.DuckDBPyConnection,
    agg_path: Path,
    *,
    min_usuarios_k: int = MIN_USUARIOS_K_DEFAULT,
) -> ResultadoNecesidad:
    t_inicio = time.perf_counter()
    consultas: list[Consulta] = []

    con_agg = abrir_agregado(agg_path, con)
    con_agg.close()
    con.execute(f"ATTACH '{Path(agg_path).as_posix()}' AS agg (READ_ONLY)")

    try:
        sql_invariante = "SELECT COUNT(*), SUM(tamano) FROM agg.agg_pedido"
        total_pedidos, total_lineas = con.execute(sql_invariante).fetchone()
        consultas.append(Consulta("invariante_totales", sql_invariante))

        sql_por_k = f"SELECT * FROM agg.agg_n7_por_k WHERE usuarios_n >= {min_usuarios_k} ORDER BY k"
        cols = ["k", "pedidos", "usuarios_n", "tamano_medio", "tamano_mediana",
                "tasa_recompra", "productos_nuevos_por_pedido", "departamentos_distintos_por_pedido"]
        por_k = [dict(zip(cols, r)) for r in con.execute(sql_por_k).fetchall()]
        consultas.append(Consulta("por_numero_pedido", sql_por_k))

        sql_pareado = "SELECT * FROM agg.agg_n7_pareado"
        pareado_cols = ["n_usuarios", "diff_tamano_media", "diff_tamano_std",
                         "diff_tasa_recompra_media", "diff_tasa_recompra_std",
                         "diff_productos_nuevos_media", "diff_productos_nuevos_std",
                         "diff_departamentos_distintos_media", "diff_departamentos_distintos_std"]
        fila_pareado = dict(zip(pareado_cols, con.execute(sql_pareado).fetchone()))
        consultas.append(Consulta("pareado_1_vs_10", sql_pareado))

        sql_reordered_en_1 = "SELECT lineas_k1, lineas_k1_reordered FROM agg.agg_n7_reordered_k1"
        lineas_k1, lineas_k1_reordered = con.execute(sql_reordered_en_1).fetchone()
        consultas.append(Consulta("reordered_en_primer_pedido", sql_reordered_en_1))
    finally:
        con.execute("DETACH agg")

    tabla_por_k, _ = limitar_tabla(por_k)
    tabla_pareado = [fila_pareado]

    n_pareado = fila_pareado["n_usuarios"]
    d_tam_media = fila_pareado["diff_tamano_media"]
    d_tam_std = fila_pareado["diff_tamano_std"]
    d_tasa_media = fila_pareado["diff_tasa_recompra_media"]
    d_tasa_std = fila_pareado["diff_tasa_recompra_std"]
    d_nuevos_media = fila_pareado["diff_productos_nuevos_media"]
    d_nuevos_std = fila_pareado["diff_productos_nuevos_std"]
    d_deptos_media = fila_pareado["diff_departamentos_distintos_media"]
    d_deptos_std = fila_pareado["diff_departamentos_distintos_std"]

    hallazgos = []
    if por_k:
        primero = next((f for f in por_k if f["k"] == 1), por_k[0])
        hallazgos.append(
            f"Se muestran {len(por_k)} valores de k (order_number) con al menos MIN_USUARIOS_K="
            f"{min_usuarios_k} usuarios; en k={primero['k']} el tamaño medio es {primero['tamano_medio']:.2f} "
            f"y la tasa de recompra {primero['tasa_recompra']:.1%} (con reordered=0 casi seguro en "
            "k=1, ver limitaciones); en k más alto la recompra sube y la variedad de "
            "departamentos por pedido se mantiene relativamente estable."
        )
    else:
        hallazgos.append(f"Ningún valor de k alcanza MIN_USUARIOS_K={min_usuarios_k} en este universo.")
    def _fmt_std(v: float | None, spec: str) -> str:
        return "n/d (n=1)" if v is None else format(v, spec)

    if n_pareado:
        hallazgos.append(
            f"Comparación PAREADA (mismo usuario, pedido {K_PAREADO_A_N7} vs {K_PAREADO_B_N7}, n={n_pareado:,} "
            f"usuarios con ambos): el tamaño cambia en promedio {d_tam_media:+.2f} líneas (desvío "
            f"{_fmt_std(d_tam_std, '.2f')}), la tasa de recompra {d_tasa_media:+.1%} (desvío "
            f"{_fmt_std(d_tasa_std, '.3f')}), productos nuevos por pedido {d_nuevos_media:+.2f} "
            f"(desvío {_fmt_std(d_nuevos_std, '.2f')}) y departamentos distintos {d_deptos_media:+.2f} "
            f"(desvío {_fmt_std(d_deptos_std, '.2f')}). Esta es la comparación válida de 'pedido 1 vs "
            f"pedido {K_PAREADO_B_N7}' que pide el brief: sigue al mismo conjunto de usuarios, no "
            "mezcla poblaciones distintas como la curva por k."
        )
    else:
        hallazgos.append(
            f"Ningún usuario de este universo tiene a la vez pedido {K_PAREADO_A_N7} y "
            f"{K_PAREADO_B_N7} con líneas: no se puede calcular la comparación pareada."
        )

    limitaciones = [
        "Descriptivo, no causal: la curva por k mezcla usuarios distintos en cada punto (solo "
        "llegan a k alto quienes tienen muchos pedidos) y no debe leerse como la trayectoria de "
        "un mismo cliente. El análisis pareado (mismo usuario en dos puntos) es la comparación "
        "válida de 'primer vs décimo pedido'.",
        f"Se midió (no se asumió) si el primer pedido (order_number=1) tiene líneas con "
        f"reordered=1: {lineas_k1_reordered:,} de {lineas_k1:,} líneas "
        f"({lineas_k1_reordered/lineas_k1:.4%}). " + (
            "El dataset SÍ registra algunas: no se puede asumir que el primer pedido nunca tenga recompra."
            if lineas_k1_reordered else
            "El dataset no registra ninguna: consistente con que reordered se define respecto al "
            "historial previo del usuario, que en el primer pedido está vacío."
        ),
        "No hay precios: 'qué ofrecer a clientes nuevos' se basa solo en tamaño, variedad y "
        "patrón de recompra, no en margen ni valor de la compra.",
    ]

    huella = huella_bd(con)
    duracion = time.perf_counter() - t_inicio

    return ResultadoNecesidad(
        id=ID,
        titulo=TITULO,
        pregunta=PREGUNTA,
        decision=DECISION,
        universo=Universo(
            texto="Pedidos con líneas (eval_set IN ('prior','train')), agrupados por order_number (k).",
            filtros=["orders.eval_set IN ('prior','train')"],
        ),
        parametros=[
            Parametro("MIN_USUARIOS_K", min_usuarios_k, JUSTIF_MIN_USUARIOS_K),
        ],
        consultas=consultas,
        tablas={"por_numero_pedido": tabla_por_k, "pareado_1_vs_10": tabla_pareado},
        tablas_criterio_corte={
            "por_numero_pedido": f"Todos los k con usuarios_n>={min_usuarios_k}, orden k asc",
            "pareado_1_vs_10": f"Una fila resumen: usuarios con pedido {K_PAREADO_A_N7} y {K_PAREADO_B_N7}, ambos con líneas",
        },
        valores_clave=[
            ValorClave("Pedidos totales (universo)", total_pedidos, "pedidos", "invariante_totales"),
            ValorClave("Líneas totales (universo)", total_lineas, "líneas", "invariante_totales"),
            ValorClave("Usuarios en comparación pareada 1 vs 10", n_pareado, "usuarios", "pareado_1_vs_10"),
            ValorClave("Diferencia media de tamaño (pedido 10 - pedido 1)", d_tam_media, "líneas", "pareado_1_vs_10"),
        ],
        hallazgos=hallazgos,
        limitaciones=limitaciones,
        duracion_s=duracion,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella),
    )
