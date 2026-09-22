"""N8 — Rutinarios vs exploradores.

Pregunta: ¿qué usuarios repiten casi siempre los mismos productos y cuáles
prueban variedad?
Decisión: a quién ofrecer suscripción/"repetir pedido" y a quién novedades.

Universo: usuarios con al menos W pedidos con líneas. VENTANA FIJA: se usan
solo los primeros W pedidos con líneas de cada usuario (mismo W para todos),
para que quien tiene más historial no "gane" variedad solo por tener más
oportunidades de probar cosas nuevas.

Usa `agg_usuario` (precalculado, ver `src.analytics.agregados`): la ventana
fija (productos distintos en los primeros W pedidos) y la tasa de recompra
del historial completo por usuario, calculadas en vivo, tardan ~5s
combinadas — el mismo umbral del brief.
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
)

ID = "N8"
TITULO = "Rutinarios vs exploradores"
PREGUNTA = "¿Qué usuarios repiten casi siempre los mismos productos y cuáles exploran variedad, controlando por exposición?"
DECISION = "A quién ofrecer suscripción/'repetir pedido' y a quién mostrar novedades."

W_DEFAULT = 5
PERCENTIL_RUTINARIO_DEFAULT = 0.25
PERCENTIL_EXPLORADOR_DEFAULT = 0.75

JUSTIF_W = (
    "W=5 es el default sugerido en el brief. Medido sobre la distribución real de pedidos con "
    "líneas por usuario (usuarios con >=W: W=3 -> 100.0%, W=5 -> 84.9% (175,072 de 206,209), "
    "W=10 -> 52.1%, W=20 -> 25.6%): W=5 cubre a la gran mayoría de usuarios (84.9%) dejando "
    "31,137 (15.1%) fuera por no alcanzar la ventana mínima, sin exigir un historial tan largo "
    "como W=10 (que perdería casi la mitad de los usuarios)."
)


def calcular(
    con: duckdb.DuckDBPyConnection,
    agg_path: Path,
    *,
    w: int = W_DEFAULT,
    percentil_rutinario: float = PERCENTIL_RUTINARIO_DEFAULT,
    percentil_explorador: float = PERCENTIL_EXPLORADOR_DEFAULT,
) -> ResultadoNecesidad:
    t_inicio = time.perf_counter()
    consultas: list[Consulta] = []

    con_agg = abrir_agregado(agg_path, con)
    meta = dict(con_agg.execute("SELECT clave, valor FROM agg_meta").fetchall())
    con_agg.close()
    w_agregado = int(meta["n8_w"])
    if w != w_agregado:
        raise ValueError(
            f"W={w} no coincide con el W={w_agregado} usado al construir agg_usuario. "
            "Reconstruye con --w-n8 o usa el mismo valor."
        )

    con.execute(f"ATTACH '{Path(agg_path).as_posix()}' AS agg (READ_ONLY)")
    try:
        sql_total = "SELECT COUNT(*) FROM agg.agg_usuario"
        total_usuarios = con.execute(sql_total).fetchone()[0]
        consultas.append(Consulta("total_usuarios", sql_total))

        sql_clasificables = f"""
        CREATE TEMP TABLE n8_clasificables AS
        SELECT user_id, pedidos_totales, mediana_intervalo, tasa_recompra_historial,
               pedidos_en_ventana, productos_distintos_ventana, lineas_ventana,
               lineas_recompra_ventana, lineas_nuevas_ventana,
               productos_distintos_ventana * 1.0 / lineas_ventana AS indice_novedad,
               lineas_ventana * 1.0 / pedidos_en_ventana AS tamano_medio_ventana,
               lineas_recompra_ventana * 1.0 / lineas_ventana AS tasa_recompra_ventana
        FROM agg.agg_usuario
        WHERE pedidos_en_ventana >= {w}
        """
        con.execute(sql_clasificables)
        consultas.append(Consulta("clasificables", sql_clasificables.strip()))

        sql_n_clasificables = "SELECT COUNT(*) FROM n8_clasificables"
        n_clasificables = con.execute(sql_n_clasificables).fetchone()[0]
        consultas.append(Consulta("n_clasificables", sql_n_clasificables))

        sql_percentiles = f"""
        SELECT PERCENTILE_CONT({percentil_rutinario}) WITHIN GROUP (ORDER BY indice_novedad),
               PERCENTILE_CONT({percentil_explorador}) WITHIN GROUP (ORDER BY indice_novedad)
        FROM n8_clasificables
        """
        p_bajo, p_alto = con.execute(sql_percentiles).fetchone()
        consultas.append(Consulta("percentiles_indice_novedad", sql_percentiles.strip()))

        cols_perfil = ["clase", "n_usuarios", "tamano_medio_pedido", "mediana_intervalo_entre_pedidos",
                       "pedidos_por_usuario_promedio", "tasa_recompra_historial_promedio"]
        if n_clasificables == 0:
            # Sin usuarios clasificables, los percentiles son NULL: no hay
            # nada que perfilar, no rompemos la consulta con NULL en el CASE.
            perfil = []
            sql_perfil = "-- n_clasificables=0: no se ejecutó (percentiles indefinidos sobre un conjunto vacío)"
        else:
            sql_perfil = f"""
            SELECT
                CASE WHEN indice_novedad <= {p_bajo} THEN 'rutinario'
                     WHEN indice_novedad >= {p_alto} THEN 'explorador'
                     ELSE 'intermedio' END AS clase,
                COUNT(*) AS n_usuarios,
                AVG(tamano_medio_ventana) AS tamano_medio_pedido,
                MEDIAN(mediana_intervalo) AS mediana_intervalo_entre_pedidos,
                AVG(pedidos_totales) AS pedidos_por_usuario_promedio,
                AVG(tasa_recompra_historial) AS tasa_recompra_historial_promedio
            FROM n8_clasificables
            GROUP BY 1
            ORDER BY 1
            """
            perfil = [dict(zip(cols_perfil, r)) for r in con.execute(sql_perfil).fetchall()]
        consultas.append(Consulta("perfil_por_clase", sql_perfil.strip()))

        sql_histograma = """
        SELECT CAST(FLOOR(indice_novedad * 10) AS INTEGER) AS bin_decimo, COUNT(*) AS n_usuarios
        FROM n8_clasificables GROUP BY 1 ORDER BY 1
        """
        histograma = [{"bin_decimo": b, "n_usuarios": n} for b, n in con.execute(sql_histograma).fetchall()]
        consultas.append(Consulta("histograma_indice_novedad", sql_histograma.strip()))

        sql_consistencia = "SELECT COUNT(*) FILTER (WHERE productos_distintos_ventana = lineas_nuevas_ventana), COUNT(*) FROM n8_clasificables"
        n_consistentes, n_total_c = con.execute(sql_consistencia).fetchone()
        consultas.append(Consulta("consistencia_medida", sql_consistencia))

        sql_pearson = "SELECT CORR(indice_novedad, tamano_medio_ventana) FROM n8_clasificables"
        pearson = con.execute(sql_pearson).fetchone()[0]
        consultas.append(Consulta("correlacion_pearson_confusor", sql_pearson))

        con.execute("DROP TABLE n8_clasificables")
    finally:
        con.execute("DETACH agg")

    n_excluidos = total_usuarios - n_clasificables
    pct_clasificables = n_clasificables / total_usuarios if total_usuarios else 0.0
    pct_consistentes = n_consistentes / n_total_c if n_total_c else 0.0

    hallazgos = [
        f"De {total_usuarios:,} usuarios, {n_clasificables:,} ({pct_clasificables:.1%}) "
        f"tienen al menos W={w} pedidos con líneas y se clasifican con la ventana fija de sus "
        f"primeros {w} pedidos; los {n_excluidos:,} restantes se excluyen.",
    ]
    if n_clasificables:
        hallazgos.append(
            f"Los cortes de índice_novedad (por construcción, tamaños de grupo fijados por percentil): "
            f"'rutinario' <= {p_bajo:.3f} (percentil {int(percentil_rutinario*100)}), 'explorador' >= "
            f"{p_alto:.3f} (percentil {int(percentil_explorador*100)})."
        )
    hallazgos.append(
        f"En {n_consistentes:,} de {n_total_c:,} usuarios ({pct_consistentes:.1%}), el número "
        "de productos distintos en la ventana coincide EXACTAMENTE con el número de líneas "
        "reordered=0 en la ventana (medido, no asumido: es lo esperable porque la ventana son los "
        "primeros pedidos del usuario, pero se verifica en vez de darlo por hecho)."
    )
    hallazgos.append(
        "Correlación de Pearson entre índice_novedad y tamaño medio de pedido (posible confusor): "
        + (f"{pearson:.3f}." if pearson is not None else "indefinida (menos de 2 usuarios clasificables).")
    )
    for fila in perfil:
        hallazgos.append(
            f"Perfil '{fila['clase']}' (n={fila['n_usuarios']:,}): tamaño medio de pedido "
            f"{fila['tamano_medio_pedido']:.2f}, mediana de intervalo entre pedidos "
            f"{fila['mediana_intervalo_entre_pedidos']:.1f} días, {fila['pedidos_por_usuario_promedio']:.1f} "
            f"pedidos por usuario (historial completo), tasa de recompra histórica "
            f"{fila['tasa_recompra_historial_promedio']:.1%}."
        )

    limitaciones = [
        "Los tamaños de los grupos 'rutinario'/'explorador' son fijos por construcción (percentiles "
        "25/75): no reflejan una proporción natural de la población, siempre serán ~25%/~25% (más "
        "el grupo intermedio ~50%). Por eso se reporta también el histograma completo y los cortes "
        "absolutos.",
        "El tamaño medio de pedido es un confusor conocido: pedidos más grandes tienen más "
        "oportunidades de incluir un producto nuevo, lo que puede inflar índice_novedad sin que "
        "sea 'exploración' en el sentido buscado. Se reporta la correlación de Pearson entre ambos "
        "para que quede explícito, no se controla estadísticamente en esta fase.",
        "No hay precios: la clasificación es de variedad y patrón de compra, no de valor.",
    ]

    huella = huella_bd(con)
    duracion = time.perf_counter() - t_inicio

    return ResultadoNecesidad(
        id=ID,
        titulo=TITULO,
        pregunta=PREGUNTA,
        decision=DECISION,
        universo=Universo(
            texto=f"Usuarios con >= W={w} pedidos con líneas; ventana fija de sus primeros {w} pedidos.",
            filtros=[f"pedidos_en_ventana >= {w}"],
        ),
        parametros=[
            Parametro("W", w, JUSTIF_W),
            Parametro("percentil_rutinario", percentil_rutinario, "Cuartil inferior estándar de índice_novedad."),
            Parametro("percentil_explorador", percentil_explorador, "Cuartil superior estándar de índice_novedad."),
        ],
        consultas=consultas,
        tablas={"perfil_por_clase": perfil, "histograma_indice_novedad": histograma},
        tablas_criterio_corte={
            "perfil_por_clase": "Las 3 clases (rutinario/intermedio/explorador), agregadas sobre usuarios clasificables",
            "histograma_indice_novedad": "Bins de ancho 0.1 sobre índice_novedad (0.0-0.1, 0.1-0.2, ...), orden bin asc",
        },
        valores_clave=[
            ValorClave("Usuarios clasificables", n_clasificables, "usuarios", "n_clasificables"),
            ValorClave("Usuarios excluidos por W", n_excluidos, "usuarios", "total_usuarios"),
            ValorClave("Corte rutinario (percentil bajo)", p_bajo, "índice_novedad", "percentiles_indice_novedad"),
            ValorClave("Corte explorador (percentil alto)", p_alto, "índice_novedad", "percentiles_indice_novedad"),
            ValorClave("Correlación Pearson índice_novedad vs tamaño", pearson, "correlación", "correlacion_pearson_confusor"),
            ValorClave("Consistencia productos_distintos == reordered=0", pct_consistentes, "fracción", "consistencia_medida"),
        ],
        hallazgos=hallazgos,
        limitaciones=limitaciones,
        duracion_s=duracion,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella),
    )
