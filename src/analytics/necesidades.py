"""Las 10 necesidades del proyecto (Fase 24): una sola consulta cada una.

Reemplazan a las 11 necesidades anteriores (N1-N11 de las Fases 2A/2B,
`n1_recompra.py` ... `n11_prediccion.py` y su agregado
`data/instacart_agg.duckdb`), que mezclaban varias preguntas en una ("y",
"vs.") y obligaban a combinar varias consultas. Cada necesidad nueva es UNA
pregunta simple y verificable que se responde con UNA sentencia SELECT.

Cada necesidad se declara como datos (`DefNecesidad`): la pregunta, el SQL de
referencia (que reusa los JOINs y reglas de `src.llm.esquema_instacart.
REGLAS_DE_NEGOCIO`) y una función que redacta en lenguaje natural el
resultado crudo. `calcular()` ejecuta el SQL y arma el mismo
`ResultadoNecesidad` de siempre (contrato de la Fase 2A), así que el
dashboard, el PDF, la entrega y el juez de iteraciones lo consumen sin
caminos nuevos:

- SQL de referencia      -> `consultas` (una sola: "respuesta")
- resultado crudo        -> `tablas["respuesta"]` (filas tal cual de DuckDB)
- redacción              -> `hallazgos` (texto generado por código, sin IA)
- respuesta en sí        -> `valores_clave`

"Unidades" (N1, N8): el dataset no trae cantidades; cada fila de
`order_products` es un producto dentro de un pedido, así que unidades =
líneas de pedido. Se declara en las limitaciones.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
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

#: Nombre de la única consulta de cada necesidad (y de su tabla).
CONSULTA = "respuesta"

#: Forma de la respuesta esperada (la usan el dashboard y el chequeo
#: automático del juez, `src.llm.retroalimentacion`):
#: - "lista": las `filas_esperadas` filas son la respuesta (un ranking).
#: - "unico": la respuesta es la PRIMERA fila (el resto es contexto: ranking).
#: - "escalar": una fila, la respuesta es `columna_valor`.
LISTA, UNICO, ESCALAR = "lista", "unico", "escalar"

TOP_N_DEFAULT = 10
MIN_COMPRAS_DEFAULT = 50

_LIMITACION_SIN_PRECIOS = (
    "El dataset no tiene precios ni montos: «más vendido» se mide en unidades (líneas de pedido), "
    "nunca en dinero."
)
_LIMITACION_UNIDADES = (
    "No hay columna de cantidad: cada fila de order_products es un producto dentro de un pedido, así "
    "que una unidad = una línea de pedido (comprar 3 plátanos en un pedido cuenta como 1)."
)
_UNIVERSO_LINEAS = "Líneas de pedido: vista order_products (order_products__prior UNION ALL order_products__train)."


@dataclass(frozen=True)
class DefNecesidad:
    id: str
    titulo: str
    pregunta: str
    decision: str
    #: SQL de referencia; puede llevar `{top_n}`/`{min_compras}` (ver `sql()`).
    sql_plantilla: str
    universo: str
    filtros: tuple[str, ...]
    limitaciones: tuple[str, ...]
    criterio_corte: str
    tipo: str
    #: Columna con el nombre de la entidad (producto/pasillo/departamento), o None (escalar).
    columna_nombre: str | None
    #: Columna con la métrica que responde la pregunta.
    columna_valor: str
    #: "producto", "departamento", "pasillo" o None: qué se identifica por nombre.
    entidad: str | None
    #: Filas que pide la pregunta (10 en "los 10 productos", 1 en "¿cuál es...?"),
    #: o None si la pregunta no fija cuántas ("¿cuáles tienen...?").
    filas_esperadas: int | None
    #: (filas como dicts, valores de los parámetros) -> (hallazgos, valores_clave).
    redactar: Callable[[list[dict], dict], tuple[list[str], list[ValorClave]]]
    #: Parámetros de la plantilla SQL con su valor por defecto y justificación.
    parametros: tuple[tuple[str, object, str], ...] = field(default_factory=tuple)

    def valores_parametros(self, **overrides) -> dict[str, object]:
        valores = {nombre: valor for nombre, valor, _ in self.parametros}
        desconocidos = set(overrides) - set(valores)
        if desconocidos:
            raise ValueError(f"{self.id} no tiene los parámetros {sorted(desconocidos)}")
        valores.update(overrides)
        return valores

    def sql(self, **overrides) -> str:
        return self.sql_plantilla.format(**self.valores_parametros(**overrides)).strip()


# ---------------------------------------------------------------------------
# Formato de la redacción (texto generado por código, nunca por un modelo)
# ---------------------------------------------------------------------------

def _n(v: object) -> str:
    return f"{v:,}" if isinstance(v, int) else str(v)


def _pct(fraccion: float, decimales: int = 1) -> str:
    return f"{fraccion * 100:.{decimales}f}%"


def _vc(etiqueta: str, valor: object, unidad: str) -> ValorClave:
    return ValorClave(etiqueta, valor, unidad, CONSULTA)


def _sin_filas(entidad: str) -> tuple[list[str], list[ValorClave]]:
    return [f"La consulta no devolvió ningún {entidad} con estos datos."], []


def _ranking(filas: list[dict], nombre: str, valor: str, unidad: str, *, desde: int = 1) -> str:
    """"1. A (n unidad); 2. B (...)", numerado desde la posición `desde`."""
    return "; ".join(f"{i}. {f[nombre]} ({_n(f[valor])} {unidad})" for i, f in enumerate(filas, desde))


def _redactar_n1(filas: list[dict], _params: dict) -> tuple[list[str], list[ValorClave]]:
    if not filas:
        return _sin_filas("producto")
    top = filas[0]
    hallazgos = [
        f"Los {len(filas)} productos más vendidos por número de unidades son: "
        f"{_ranking(filas, 'product_name', 'unidades', 'unidades')}.",
        f"El más vendido es {top['product_name']}, con {_n(top['unidades'])} unidades.",
    ]
    return hallazgos, [
        _vc("Producto más vendido", top["product_name"], "producto"),
        _vc("Unidades del más vendido", top["unidades"], "unidades"),
        _vc(f"Top {len(filas)} por unidades", [f["product_name"] for f in filas], "productos"),
    ]


def _redactar_n2(filas: list[dict], _params: dict) -> tuple[list[str], list[ValorClave]]:
    if not filas:
        return _sin_filas("departamento")
    top = filas[0]
    hallazgos = [
        f"El departamento con más pedidos es {top['department']}: aparece en {_n(top['pedidos'])} pedidos "
        "distintos (pedidos que tienen al menos un producto de ese departamento).",
    ]
    if len(filas) > 1:
        hallazgos.append(f"Le siguen: {_ranking(filas[1:5], 'department', 'pedidos', 'pedidos', desde=2)}.")
    return hallazgos, [
        _vc("Departamento con más pedidos", top["department"], "departamento"),
        _vc("Pedidos de ese departamento", top["pedidos"], "pedidos"),
    ]


def _redactar_tasa(filas: list[dict], params: dict, extremo: str) -> tuple[list[str], list[ValorClave]]:
    min_compras = params["min_compras"]
    if not filas:
        return [f"Ningún producto tiene al menos {min_compras} compras con estos datos."], []
    top = filas[0]
    lista = "; ".join(
        f"{i}. {f['product_name']} ({_pct(f['tasa_recompra'])}: {_n(f['recompras'])} de {_n(f['veces_comprado'])})"
        for i, f in enumerate(filas, 1)
    )
    hallazgos = [
        f"De los productos con al menos {min_compras} compras, los {len(filas)} con la tasa de recompra más "
        f"{extremo} son: {lista}.",
        f"El de tasa más {extremo} es {top['product_name']}: {_n(top['recompras'])} recompras de "
        f"{_n(top['veces_comprado'])} compras ({_pct(top['tasa_recompra'])}).",
    ]
    if extremo == "baja":
        ceros = [f for f in filas if f["recompras"] == 0]
        if ceros:
            cuantos = f"Exactamente {len(ceros)}" if len(ceros) < len(filas) else f"Al menos {len(ceros)}"
            hallazgos.append(f"{cuantos} productos con al menos {min_compras} compras nunca se recompraron "
                             "(tasa 0%); el orden entre ellos es por número de compras.")
    return hallazgos, [
        _vc(f"Producto con la tasa de recompra más {extremo}", top["product_name"], "producto"),
        _vc(f"Tasa de recompra más {extremo}", top["tasa_recompra"], "fracción"),
        _vc(f"Top {len(filas)} por tasa más {extremo}", [f["product_name"] for f in filas], "productos"),
    ]


def _redactar_n5(filas: list[dict], _params: dict) -> tuple[list[str], list[ValorClave]]:
    f = filas[0] if filas else {}
    if f.get("promedio_productos_por_pedido") is None:
        return ["No hay pedidos con productos en estos datos."], []
    return [
        f"El tamaño promedio del carrito es {f['promedio_productos_por_pedido']:.2f} productos por pedido "
        f"({_n(f['lineas'])} líneas de pedido entre {_n(f['pedidos'])} pedidos con productos).",
    ], [
        _vc("Productos por pedido (promedio)", f["promedio_productos_por_pedido"], "productos/pedido"),
        _vc("Pedidos con productos", f["pedidos"], "pedidos"),
    ]


def _redactar_n6(filas: list[dict], _params: dict) -> tuple[list[str], list[ValorClave]]:
    f = filas[0] if filas else {}
    if f.get("promedio_dias_entre_pedidos") is None:
        return ["No hay intervalos entre pedidos en estos datos."], []
    return [
        f"En promedio, un cliente vuelve a comprar cada {f['promedio_dias_entre_pedidos']:.2f} días "
        f"(promedio de days_since_prior_order sobre {_n(f['pedidos_con_intervalo'])} pedidos que tienen un "
        "pedido previo).",
    ], [
        _vc("Días entre pedidos (promedio)", f["promedio_dias_entre_pedidos"], "días"),
        _vc("Pedidos con intervalo", f["pedidos_con_intervalo"], "pedidos"),
    ]


def _redactar_pasillo(filas: list[dict], valor: str, unidad: str, que: str) -> tuple[list[str], list[ValorClave]]:
    if not filas:
        return _sin_filas("pasillo")
    top = filas[0]
    hallazgos = [f"El pasillo con {que} es {top['aisle']}, con {_n(top[valor])} {unidad}."]
    if top["aisle"] == "missing" and len(filas) > 1:
        hallazgos.append(
            "«missing» es el pasillo comodín de Instacart para productos sin pasillo asignado, no un pasillo "
            f"real; el primer pasillo real es {filas[1]['aisle']} ({_n(filas[1][valor])} {unidad})."
        )
    hallazgos.append(f"Ranking: {_ranking(filas, 'aisle', valor, unidad)}.")
    return hallazgos, [
        _vc(f"Pasillo con {que}", top["aisle"], "pasillo"),
        _vc(unidad.capitalize(), top[valor], unidad),
    ]


def _redactar_n9(filas: list[dict], _params: dict) -> tuple[list[str], list[ValorClave]]:
    if not filas:
        return _sin_filas("producto")
    top = filas[0]
    hallazgos = [
        f"El producto que más veces se agrega primero al carrito (add_to_cart_order = 1) es "
        f"{top['product_name']}: {_n(top['veces_primero'])} veces.",
    ]
    if len(filas) > 1:
        hallazgos.append(f"Le siguen: {_ranking(filas[1:5], 'product_name', 'veces_primero', 'veces', desde=2)}.")
    return hallazgos, [
        _vc("Producto más agregado primero", top["product_name"], "producto"),
        _vc("Veces que fue el primero", top["veces_primero"], "veces"),
    ]


def _redactar_n10(filas: list[dict], _params: dict) -> tuple[list[str], list[ValorClave]]:
    f = filas[0] if filas else {}
    if f.get("pct_lineas_recompradas") is None:
        return ["No hay líneas de pedido en estos datos."], []
    return [
        f"El {f['pct_lineas_recompradas']:.2f}% de todas las líneas de pedido corresponde a productos "
        f"recomprados (reordered = 1): {_n(f['lineas_recompradas'])} de {_n(f['lineas_totales'])} líneas.",
    ], [
        _vc("% de líneas recompradas", f["pct_lineas_recompradas"], "%"),
        _vc("Líneas recompradas", f["lineas_recompradas"], "líneas"),
        _vc("Líneas totales", f["lineas_totales"], "líneas"),
    ]


_P_TOP_N = ("top_n", TOP_N_DEFAULT, "La pregunta pide los 10 primeros.")
_P_TOP_N_CONTEXTO = ("top_n", TOP_N_DEFAULT,
                     "La pregunta pide uno solo (la primera fila); las 10 primeras se guardan como contexto "
                     "para el dashboard.")
_P_TOP_N_CONVENCION = ("top_n", TOP_N_DEFAULT,
                       "La pregunta no fija cuántos («¿cuáles...?»): la referencia guarda los 10 primeros por "
                       "convención, y el chequeo automático compara los primeros nombres del intento contra ellos.")
_P_MIN_COMPRAS = ("min_compras", MIN_COMPRAS_DEFAULT,
                  "Lo fija la pregunta («al menos 50 compras»): con menos compras, una tasa de 1/1 o 0/1 no "
                  "significa nada. Es el mismo umbral que ya usaba REGLAS_DE_NEGOCIO.")

DEFINICIONES: dict[str, DefNecesidad] = {d.id: d for d in (
    DefNecesidad(
        id="N1", titulo="Productos más vendidos",
        pregunta="¿Cuáles son los 10 productos más vendidos por número de unidades?",
        decision="Qué productos asegurar en inventario por volumen.",
        sql_plantilla="""
SELECT p.product_name, COUNT(*) AS unidades
FROM order_products op
JOIN products p ON op.product_id = p.product_id
GROUP BY p.product_name
ORDER BY unidades DESC, p.product_name
LIMIT {top_n}""",
        universo=_UNIVERSO_LINEAS, filtros=("ninguno: todas las líneas prior + train",),
        limitaciones=(_LIMITACION_UNIDADES, _LIMITACION_SIN_PRECIOS),
        criterio_corte="Los top_n productos por unidades (desc), desempate por nombre",
        tipo=LISTA, columna_nombre="product_name", columna_valor="unidades", entidad="producto",
        filas_esperadas=TOP_N_DEFAULT, redactar=_redactar_n1, parametros=(_P_TOP_N,),
    ),
    DefNecesidad(
        id="N2", titulo="Departamento con más pedidos",
        pregunta="¿Qué departamento tiene más pedidos en total?",
        decision="Qué departamento conviene priorizar en surtido y visibilidad.",
        sql_plantilla="""
SELECT d.department, COUNT(DISTINCT op.order_id) AS pedidos
FROM order_products op
JOIN products p ON op.product_id = p.product_id
JOIN departments d ON p.department_id = d.department_id
GROUP BY d.department
ORDER BY pedidos DESC, d.department
LIMIT {top_n}""",
        universo=_UNIVERSO_LINEAS + " Se cuentan pedidos DISTINTOS por departamento.",
        filtros=("ninguno: todas las líneas prior + train",),
        limitaciones=(
            "«Pedidos de un departamento» = pedidos distintos con al menos un producto de ese departamento "
            "(COUNT(DISTINCT order_id)). Un pedido con productos de 3 departamentos cuenta en los 3. Contar "
            "líneas (unidades) en vez de pedidos distintos es otra métrica.",
            "Los pedidos con eval_set = 'test' no tienen productos y no cuentan en ningún departamento.",
        ),
        criterio_corte="Los top_n departamentos por pedidos distintos (desc), desempate por nombre",
        tipo=UNICO, columna_nombre="department", columna_valor="pedidos", entidad="departamento",
        filas_esperadas=1, redactar=_redactar_n2, parametros=(_P_TOP_N_CONTEXTO,),
    ),
    DefNecesidad(
        id="N3", titulo="Tasa de recompra más alta",
        pregunta="De los productos con al menos 50 compras, ¿cuáles tienen la tasa de recompra más alta?",
        decision="Qué productos tienen clientela fiel (candidatos a suscripción o recordatorio).",
        sql_plantilla="""
SELECT p.product_name,
       COUNT(*) AS veces_comprado,
       SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) AS recompras,
       SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS tasa_recompra
FROM order_products op
JOIN products p ON op.product_id = p.product_id
GROUP BY p.product_name
HAVING COUNT(*) >= {min_compras}
ORDER BY tasa_recompra DESC, veces_comprado DESC, p.product_name
LIMIT {top_n}""",
        universo=_UNIVERSO_LINEAS, filtros=("productos con al menos min_compras líneas",),
        limitaciones=(
            "Tasa de recompra = líneas con reordered = 1 / líneas totales del producto (recompras / veces "
            "comprado), no un conteo absoluto de recompras.",
            "reordered vale 0 en la primera compra de cada usuario: un producto comprado por muchos usuarios "
            "distintos una sola vez tiene tasa baja aunque se venda mucho.",
        ),
        criterio_corte="Los top_n por tasa de recompra (desc), desempate por veces_comprado desc y nombre",
        tipo=LISTA, columna_nombre="product_name", columna_valor="tasa_recompra", entidad="producto",
        filas_esperadas=None, redactar=lambda f, p: _redactar_tasa(f, p, "alta"),
        parametros=(_P_MIN_COMPRAS, _P_TOP_N_CONVENCION),
    ),
    DefNecesidad(
        id="N4", titulo="Tasa de recompra más baja",
        pregunta="De los productos con al menos 50 compras, ¿cuáles tienen la tasa de recompra más baja?",
        decision="Qué productos se prueban y no se vuelven a comprar (revisar o retirar).",
        sql_plantilla="""
SELECT p.product_name,
       COUNT(*) AS veces_comprado,
       SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) AS recompras,
       SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS tasa_recompra
FROM order_products op
JOIN products p ON op.product_id = p.product_id
GROUP BY p.product_name
HAVING COUNT(*) >= {min_compras}
ORDER BY tasa_recompra ASC, veces_comprado DESC, p.product_name
LIMIT {top_n}""",
        universo=_UNIVERSO_LINEAS, filtros=("productos con al menos min_compras líneas",),
        limitaciones=(
            "Tasa de recompra = líneas con reordered = 1 / líneas totales del producto. Ordenar por el "
            "número de líneas con reordered = 0 favorece a los productos más populares, no a los de tasa baja.",
            "Hay empates en tasa 0: entre ellos el orden (por veces_comprado desc y nombre) es una convención, "
            "no parte de la respuesta.",
        ),
        criterio_corte="Los top_n por tasa de recompra (asc), desempate por veces_comprado desc y nombre",
        tipo=LISTA, columna_nombre="product_name", columna_valor="tasa_recompra", entidad="producto",
        filas_esperadas=None, redactar=lambda f, p: _redactar_tasa(f, p, "baja"),
        parametros=(_P_MIN_COMPRAS, _P_TOP_N_CONVENCION),
    ),
    DefNecesidad(
        id="N5", titulo="Tamaño promedio del carrito",
        pregunta="¿Cuál es el tamaño promedio del carrito (productos por pedido)?",
        decision="Referencia para metas de productos por pedido (sin montos: no hay precios).",
        sql_plantilla="""
SELECT AVG(n_productos) AS promedio_productos_por_pedido,
       COUNT(*) AS pedidos,
       SUM(n_productos) AS lineas
FROM (
    SELECT order_id, COUNT(*) AS n_productos
    FROM order_products
    GROUP BY order_id
) t""",
        universo="Pedidos con productos (prior + train): cada pedido de order_products con su número de líneas.",
        filtros=("se excluyen los pedidos eval_set = 'test' (no tienen productos)",),
        limitaciones=(_LIMITACION_UNIDADES,
                      "Es la media: la distribución del tamaño del pedido está sesgada a la derecha (unos pocos "
                      "pedidos muy grandes la suben)."),
        criterio_corte="Una fila",
        tipo=ESCALAR, columna_nombre=None, columna_valor="promedio_productos_por_pedido", entidad=None,
        filas_esperadas=1, redactar=_redactar_n5,
    ),
    DefNecesidad(
        id="N6", titulo="Días entre pedidos",
        pregunta="¿Cada cuántos días en promedio un cliente vuelve a comprar?",
        decision="Cada cuánto conviene enviar un recordatorio de compra.",
        sql_plantilla="""
SELECT AVG(days_since_prior_order) AS promedio_dias_entre_pedidos,
       COUNT(days_since_prior_order) AS pedidos_con_intervalo
FROM orders""",
        universo="Todos los pedidos (orders, prior + train + test) que tienen un pedido previo.",
        filtros=("days_since_prior_order no NULL (se excluye el primer pedido de cada usuario)",),
        limitaciones=(
            "days_since_prior_order está censurado en 30: cualquier intervalo de 30 días o más vale 30, así "
            "que el promedio real es MAYOR que este.",
            "Incluye los pedidos eval_set = 'test': tienen intervalo aunque no tengan productos, y la "
            "pregunta es sobre cuándo vuelve el cliente, no sobre qué compra.",
            "Es el promedio por pedido, no por cliente: los clientes con muchos pedidos pesan más.",
        ),
        criterio_corte="Una fila",
        tipo=ESCALAR, columna_nombre=None, columna_valor="promedio_dias_entre_pedidos", entidad=None,
        filas_esperadas=1, redactar=_redactar_n6,
    ),
    DefNecesidad(
        id="N7", titulo="Pasillo con más productos distintos",
        pregunta="¿Qué pasillo tiene más productos distintos a la venta?",
        decision="Qué pasillos concentran el surtido (dónde simplificar o reforzar el catálogo).",
        sql_plantilla="""
SELECT a.aisle, COUNT(*) AS productos_distintos
FROM aisles a
JOIN products p ON p.aisle_id = a.aisle_id
GROUP BY a.aisle
ORDER BY productos_distintos DESC, a.aisle
LIMIT {top_n}""",
        universo="Catálogo: tabla products unida con aisles (productos a la venta, se hayan pedido o no).",
        filtros=("ninguno",),
        limitaciones=(
            "«A la venta» = en el catálogo (products). Contar productos distintos que sí aparecen en "
            "order_products da casi el mismo ranking en este dataset, pero es otra pregunta.",
            "El pasillo «missing» agrupa los productos sin pasillo asignado: no es un pasillo real.",
        ),
        criterio_corte="Los top_n pasillos por productos del catálogo (desc), desempate por nombre",
        tipo=UNICO, columna_nombre="aisle", columna_valor="productos_distintos", entidad="pasillo",
        filas_esperadas=1,
        redactar=lambda f, _p: _redactar_pasillo(f, "productos_distintos", "productos distintos",
                                             "más productos distintos a la venta"),
        parametros=(_P_TOP_N_CONTEXTO,),
    ),
    DefNecesidad(
        id="N8", titulo="Pasillo con más unidades vendidas",
        pregunta="¿Qué pasillo tiene más unidades vendidas en total?",
        decision="Qué pasillo mueve más volumen (reposición, espacio, personal).",
        sql_plantilla="""
SELECT a.aisle, COUNT(*) AS unidades
FROM order_products op
JOIN products p ON op.product_id = p.product_id
JOIN aisles a ON p.aisle_id = a.aisle_id
GROUP BY a.aisle
ORDER BY unidades DESC, a.aisle
LIMIT {top_n}""",
        universo=_UNIVERSO_LINEAS, filtros=("ninguno: todas las líneas prior + train",),
        limitaciones=(_LIMITACION_UNIDADES, _LIMITACION_SIN_PRECIOS),
        criterio_corte="Los top_n pasillos por unidades (desc), desempate por nombre",
        tipo=UNICO, columna_nombre="aisle", columna_valor="unidades", entidad="pasillo",
        filas_esperadas=1,
        redactar=lambda f, _p: _redactar_pasillo(f, "unidades", "unidades", "más unidades vendidas"),
        parametros=(_P_TOP_N_CONTEXTO,),
    ),
    DefNecesidad(
        id="N9", titulo="Producto que se agrega primero",
        pregunta="¿Cuál es el producto que más veces se agrega primero al carrito (add_to_cart_order = 1)?",
        decision="Qué producto motiva la compra (candidato a portada o a promoción de entrada).",
        sql_plantilla="""
SELECT p.product_name, COUNT(*) AS veces_primero
FROM order_products op
JOIN products p ON op.product_id = p.product_id
WHERE op.add_to_cart_order = 1
GROUP BY p.product_name
ORDER BY veces_primero DESC, p.product_name
LIMIT {top_n}""",
        universo=_UNIVERSO_LINEAS + " Solo la primera línea de cada pedido (add_to_cart_order = 1).",
        filtros=("add_to_cart_order = 1",),
        limitaciones=(
            "El orden de agregado al carrito refleja la interfaz de la app (recomendaciones, «comprar de "
            "nuevo») además de la intención del cliente.",
            "Los productos más vendidos en general tienden a salir primero también: no es una medida "
            "relativa a su volumen total.",
        ),
        criterio_corte="Los top_n productos por veces en la posición 1 (desc), desempate por nombre",
        tipo=UNICO, columna_nombre="product_name", columna_valor="veces_primero", entidad="producto",
        filas_esperadas=1, redactar=_redactar_n9, parametros=(_P_TOP_N_CONTEXTO,),
    ),
    DefNecesidad(
        id="N10", titulo="Porcentaje de líneas recompradas",
        pregunta="¿Qué porcentaje de todas las líneas de pedido corresponde a productos recomprados (reordered = 1)?",
        decision="Qué tanto del negocio viene de compras repetidas (peso de la fidelidad).",
        sql_plantilla="""
SELECT SUM(CASE WHEN reordered = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS pct_lineas_recompradas,
       SUM(CASE WHEN reordered = 1 THEN 1 ELSE 0 END) AS lineas_recompradas,
       COUNT(*) AS lineas_totales
FROM order_products""",
        universo=_UNIVERSO_LINEAS, filtros=("ninguno: todas las líneas prior + train",),
        limitaciones=(
            "reordered = 0 en todas las líneas del primer pedido de cada usuario (no hay compra previa): el "
            "porcentaje depende de cuántos pedidos tiene cada usuario en el dataset.",
        ),
        criterio_corte="Una fila",
        tipo=ESCALAR, columna_nombre=None, columna_valor="pct_lineas_recompradas", entidad=None,
        filas_esperadas=1, redactar=_redactar_n10,
    ),
)}

IDS: tuple[str, ...] = tuple(DEFINICIONES)


def columnas_ordenadas(necesidad_id: str, filas: list[dict]) -> list[str]:
    """Orden canónico de las columnas de la tabla de respuesta: nombre,
    métrica y el resto alfabético. El JSON se serializa con `sort_keys`
    (determinismo byte a byte), así que el orden del SELECT se pierde al leerlo
    de caché; con este orden, un resultado recién calculado y uno leído de
    disco se muestran igual (dashboard, PDF, juez)."""
    if not filas:
        return []
    d = DEFINICIONES[necesidad_id]
    primeras = [c for c in (d.columna_nombre, d.columna_valor) if c and c in filas[0]]
    return primeras + sorted(c for c in filas[0] if c not in primeras)


def calcular(necesidad_id: str, con: duckdb.DuckDBPyConnection, **parametros) -> ResultadoNecesidad:
    """Ejecuta el SQL de referencia de `necesidad_id` y arma su
    `ResultadoNecesidad`. `parametros` sobreescribe los de la plantilla
    (`top_n`, `min_compras`); solo los tests los cambian."""
    d = DEFINICIONES[necesidad_id]
    t_inicio = time.perf_counter()
    valores = d.valores_parametros(**parametros)
    sql = d.sql(**parametros)
    cursor = con.execute(sql)
    columnas = [c[0] for c in cursor.description]
    filas = [dict(zip(columnas, fila)) for fila in cursor.fetchall()]
    tabla, _ = limitar_tabla(filas)

    hallazgos, valores_clave = d.redactar(filas, valores)

    return ResultadoNecesidad(
        id=d.id,
        titulo=d.titulo,
        pregunta=d.pregunta,
        decision=d.decision,
        universo=Universo(texto=d.universo, filtros=list(d.filtros)),
        parametros=[Parametro(nombre, valores[nombre], justificacion) for nombre, _v, justificacion in d.parametros],
        consultas=[Consulta(CONSULTA, sql)],
        tablas={CONSULTA: tabla},
        tablas_criterio_corte={CONSULTA: d.criterio_corte},
        valores_clave=valores_clave,
        hallazgos=hallazgos,
        limitaciones=list(d.limitaciones),
        duracion_s=time.perf_counter() - t_inicio,
        version_duckdb=version_duckdb(),
        fecha_generacion=datetime.now(timezone.utc).isoformat(),
        huella_bd=HuellaBD(**huella_bd(con)),
    )
