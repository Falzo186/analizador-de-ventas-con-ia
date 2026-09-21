"""N5 — Productos ancla: qué se agrega primero al carrito.

Pregunta: ¿qué productos aparecen con más frecuencia como el primer artículo
de un pedido, y cuáles lo hacen más de lo que su popularidad explicaría?
Decisión: qué destacar en portada / primera pantalla.

Universo: líneas (vista order_products, prior+train).

Control por tamaño de pedido: en un pedido de 2 productos, "ser el primero"
tiene 1/2 de probabilidad por azar; en uno de 20, 1/20. Sin ese control, los
pedidos chicos inflarían artificialmente la tasa de cualquier producto que
aparezca en ellos. `índice_ancla` normaliza por esa probabilidad esperada.
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

ID = "N5"
TITULO = "Productos ancla"
PREGUNTA = "¿Qué productos aparecen como primer artículo del carrito más de lo que su popularidad explicaría?"
DECISION = "Qué producto(s) destacar en portada o primera pantalla."

MIN_APARICIONES_DEFAULT = 50

JUSTIF_MIN_APARICIONES = (
    "Misma distribución medida que MIN_LINEAS en N1 (líneas por producto: min=1, P25=18, "
    "mediana=63, P75=272, máx=491,291, pues 'apariciones' en N5 es la misma columna 'líneas' "
    "de order_products): MIN_APARICIONES=50 conserva 27,208 de 49,688 productos (54.7%) y "
    "98.7% de las líneas, con muestra suficiente para estimar tasa_primero e índice_ancla de "
    "forma estable."
)


def calcular(
    con: duckdb.DuckDBPyConnection,
    *,
    min_apariciones: int = MIN_APARICIONES_DEFAULT,
) -> ResultadoNecesidad:
    t_inicio = time.perf_counter()
    consultas: list[Consulta] = []

    sql_tam_pedido = "CREATE TEMP TABLE n5_tam_pedido AS SELECT order_id, COUNT(*) AS tam_pedido FROM order_products GROUP BY order_id"
    con.execute(sql_tam_pedido)
    consultas.append(Consulta("tam_pedido", sql_tam_pedido))

    sql_base = """
    CREATE TEMP TABLE n5_base AS
    SELECT op.product_id,
           COUNT(*) AS apariciones,
           SUM(CASE WHEN op.add_to_cart_order = 1 THEN 1 ELSE 0 END) AS veces_primero,
           SUM(CASE WHEN op.add_to_cart_order = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS tasa_primero,
           AVG(1.0 / tp.tam_pedido) AS tasa_esperada
    FROM order_products op
    JOIN n5_tam_pedido tp ON tp.order_id = op.order_id
    GROUP BY op.product_id
    """
    con.execute(sql_base)
    consultas.append(Consulta("base_producto", sql_base.strip()))

    sql_con_nombre = """
    SELECT b.product_id, p.product_name, b.apariciones, b.veces_primero, b.tasa_primero,
           b.tasa_esperada, b.tasa_primero / b.tasa_esperada AS indice_ancla
    FROM n5_base b JOIN products p ON p.product_id = b.product_id
    """
    con.execute(f"CREATE TEMP TABLE n5_completo AS {sql_con_nombre}")
    consultas.append(Consulta("completo_con_nombre", sql_con_nombre.strip()))

    sql_global = "SELECT COUNT(*), SUM(apariciones), SUM(veces_primero) FROM n5_completo"
    n_productos_total, lineas_total, veces_primero_total = con.execute(sql_global).fetchone()
    consultas.append(Consulta("global", sql_global))

    sql_top_volumen = """
    SELECT product_id, product_name, apariciones, veces_primero, tasa_primero, tasa_esperada, indice_ancla
    FROM n5_completo
    ORDER BY veces_primero DESC, product_id ASC
    LIMIT 50
    """
    cols = ["product_id", "product_name", "apariciones", "veces_primero", "tasa_primero", "tasa_esperada", "indice_ancla"]
    top_volumen = [dict(zip(cols, r)) for r in con.execute(sql_top_volumen).fetchall()]
    consultas.append(Consulta("top_50_volumen", sql_top_volumen.strip()))

    sql_elegibles_count = f"SELECT COUNT(*), SUM(apariciones) FROM n5_completo WHERE apariciones >= {min_apariciones}"
    n_elegibles, lineas_elegibles = con.execute(sql_elegibles_count).fetchone()
    lineas_elegibles = lineas_elegibles or 0
    consultas.append(Consulta("elegibles_count", sql_elegibles_count))

    sql_top_indice = f"""
    SELECT product_id, product_name, apariciones, veces_primero, tasa_primero, tasa_esperada, indice_ancla
    FROM n5_completo
    WHERE apariciones >= {min_apariciones}
    ORDER BY indice_ancla DESC, apariciones DESC, product_id ASC
    LIMIT 50
    """
    top_indice = [dict(zip(cols, r)) for r in con.execute(sql_top_indice).fetchall()]
    consultas.append(Consulta("top_50_indice_ancla", sql_top_indice.strip()))

    con.execute("DROP TABLE n5_completo")
    con.execute("DROP TABLE n5_base")
    con.execute("DROP TABLE n5_tam_pedido")

    tabla_volumen, _ = limitar_tabla(top_volumen)
    tabla_indice, _ = limitar_tabla(top_indice)

    hallazgos = [
        f"De {n_productos_total:,} productos, {n_elegibles:,} ({n_elegibles/n_productos_total:.1%}) "
        f"cumplen MIN_APARICIONES>={min_apariciones} y cubren {lineas_elegibles:,} líneas "
        f"({lineas_elegibles/lineas_total:.1%} del total).",
        f"En total, {veces_primero_total:,} líneas de {lineas_total:,} son 'primer artículo' del "
        f"pedido ({veces_primero_total/lineas_total:.1%}), lo que reconcilia con el número de "
        "pedidos con líneas del dataset (cada pedido con líneas tiene exactamente un primer artículo).",
    ]
    if top_volumen:
        hallazgos.insert(0,
            f"El producto que más veces aparece como primer artículo del carrito es "
            f"{top_volumen[0]['product_name']!r} ({top_volumen[0]['veces_primero']:,} veces, "
            f"tasa_primero={top_volumen[0]['tasa_primero']:.1%} de sus {top_volumen[0]['apariciones']:,} apariciones)."
        )
    if top_indice:
        hallazgos.append(
            f"Controlando por tamaño de pedido, el producto con mayor índice_ancla entre elegibles es "
            f"{top_indice[0]['product_name']!r} (índice_ancla={top_indice[0]['indice_ancla']:.2f}x lo "
            f"esperado por azar, con {top_indice[0]['apariciones']:,} apariciones) — no necesariamente "
            "el más frecuente en volumen."
        )
    else:
        hallazgos.append(
            f"Ningún producto cumple MIN_APARICIONES>={min_apariciones} en este universo: "
            "no se puede calcular el top de índice_ancla."
        )

    limitaciones = [
        "El orden en que un producto se agregó al carrito (add_to_cart_order) puede reflejar la "
        "interfaz de compra (por ejemplo, listas de 'comprar de nuevo' que precargan productos "
        "habituales al inicio del carrito), no necesariamente una preferencia real del usuario. "
        "Con estos datos, 'producto ancla' es una hipótesis a validar, no un hecho establecido.",
        "No hay precios: esta necesidad no afirma nada sobre el valor de destacar un producto, "
        "solo sobre la frecuencia con la que aparece primero.",
        f"MIN_APARICIONES={min_apariciones} excluye productos con muestra insuficiente para "
        "estimar tasa_primero/índice_ancla de forma estable.",
    ]

    huella = huella_bd(con)
    duracion = time.perf_counter() - t_inicio

    return ResultadoNecesidad(
        id=ID,
        titulo=TITULO,
        pregunta=PREGUNTA,
        decision=DECISION,
        universo=Universo(
            texto="Líneas de la vista order_products (prior+train).",
            filtros=["order_products__prior UNION ALL order_products__train"],
        ),
        parametros=[
            Parametro("MIN_APARICIONES", min_apariciones, JUSTIF_MIN_APARICIONES),
        ],
        consultas=consultas,
        tablas={"top_50_volumen": tabla_volumen, "top_50_indice_ancla": tabla_indice},
        tablas_criterio_corte={
            "top_50_volumen": "Top 50 por veces_primero desc (todos los productos), desempate product_id asc",
            "top_50_indice_ancla": f"Entre elegibles (apariciones>={min_apariciones}), top 50 por índice_ancla desc, "
                                   "desempate apariciones desc, product_id asc",
        },
        valores_clave=[
            ValorClave("Productos elegibles", n_elegibles, "productos", "elegibles_count"),
            ValorClave("Veces_primero total (= pedidos con líneas)", veces_primero_total, "líneas", "global"),
            ValorClave("Producto top por volumen",
                       top_volumen[0]["product_name"] if top_volumen else None, "producto", "top_50_volumen"),
            ValorClave("Producto top por índice_ancla",
                       top_indice[0]["product_name"] if top_indice else None, "producto", "top_50_indice_ancla"),
        ],
        hallazgos=hallazgos,
        limitaciones=limitaciones,
        duracion_s=duracion,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella),
    )
