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

Fase 28: N1, N2, N8, N9 y N10 se reemplazaron por preguntas más analíticas
(clientes de compra sostenida, tasa de recompra por departamento, hora pico,
pares de productos que se compran juntos y pedidos 100 % recomprados vs. 100 %
nuevos). Siguen siendo UNA consulta cada una; N3-N7 no cambiaron. Para
declararlas, `DefNecesidad` admite un identificador numérico (`user_id`, la
hora), un par de nombres (N9) y más de una cifra de respuesta (N10).
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
#: N1: pedidos mínimos de un cliente (la mediana por cliente es 10).
MIN_PEDIDOS_CLIENTE_DEFAULT = 10
#: N9: pedidos mínimos de cada producto del par (ver la justificación del parámetro).
MIN_PEDIDOS_PRODUCTO_PAR_DEFAULT = 10_000

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
    #: Fase 28: False si la entidad se identifica con un número (user_id, la
    #: hora), no con un nombre de texto.
    nombre_es_texto: bool = True
    #: Fase 28: columnas que, junto con `columna_nombre`, identifican la
    #: respuesta (N9: el par producto_a + producto_b).
    columnas_nombre_extra: tuple[str, ...] = ()
    #: Fase 28: otras cifras que también son la respuesta (N10: los dos %).
    columnas_valor_extra: tuple[str, ...] = ()
    #: Fase 29: límite (s) de la guarda de DuckDB cuando un modelo genera el SQL
    #: de esta necesidad (panel de iteraciones y entrega de IA). None = el del
    #: chat (`safety_instacart.TIMEOUT_S_DEFAULT`, 10 s).
    timeout_consulta_s: float | None = None

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


def _redactar_n1(filas: list[dict], params: dict) -> tuple[list[str], list[ValorClave]]:
    minimo = params["min_pedidos"]
    if not filas:
        return [f"Ningún cliente tiene al menos {minimo} pedidos con estos datos."], []
    top = filas[0]
    lista = "; ".join(
        f"{i}. cliente {f['user_id']} ({_n(f['total_pedidos'])} pedidos, cada "
        f"{f['promedio_dias_entre_pedidos']:.2f} días en promedio)"
        for i, f in enumerate(filas, 1)
    )
    hallazgos = [
        f"Entre los clientes con al menos {minimo} pedidos, los {len(filas)} de compra más sostenida (más "
        f"pedidos y, a igualdad, menos días entre pedidos consecutivos) son: {lista}.",
        f"El primero es el cliente {top['user_id']}: {_n(top['total_pedidos'])} pedidos, con "
        f"{top['promedio_dias_entre_pedidos']:.2f} días en promedio entre pedidos consecutivos.",
    ]
    if len({f["total_pedidos"] for f in filas}) == 1:
        hallazgos.append(
            f"Los {len(filas)} tienen {_n(top['total_pedidos'])} pedidos (el dataset guarda como máximo 100 "
            "por cliente), así que entre ellos el orden lo decide el promedio de días entre pedidos."
        )
    return hallazgos, [
        _vc("Cliente de compra más sostenida", str(top["user_id"]), "user_id"),
        _vc("Pedidos de ese cliente", top["total_pedidos"], "pedidos"),
        _vc("Días promedio entre sus pedidos", top["promedio_dias_entre_pedidos"], "días"),
        _vc(f"Top {len(filas)} clientes", [str(f["user_id"]) for f in filas], "user_id"),
    ]


def _redactar_n2(filas: list[dict], _params: dict) -> tuple[list[str], list[ValorClave]]:
    if not filas:
        return _sin_filas("departamento")
    top = filas[0]
    hallazgos = [
        f"El departamento con la tasa de recompra promedio más alta es {top['department']}: el "
        f"{_pct(top['tasa_recompra'])} de sus líneas de pedido son recompras ({_n(top['recompras'])} de "
        f"{_n(top['veces_comprado'])}).",
    ]
    if len(filas) > 1:
        siguen = "; ".join(f"{i}. {f['department']} ({_pct(f['tasa_recompra'])})"
                           for i, f in enumerate(filas[1:5], 2))
        hallazgos.append(f"Le siguen: {siguen}.")
    return hallazgos, [
        _vc("Departamento con la tasa de recompra más alta", top["department"], "departamento"),
        _vc("Tasa de recompra de ese departamento", top["tasa_recompra"], "fracción"),
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


def _redactar_n8(filas: list[dict], _params: dict) -> tuple[list[str], list[ValorClave]]:
    if not filas:
        return _sin_filas("pedido")
    top = filas[0]
    total = sum(f["pedidos"] for f in filas)
    hallazgos = [
        f"La hora del día con más pedidos es la hora {top['order_hour_of_day']} (de {top['order_hour_of_day']}:00 a "
        f"{top['order_hour_of_day']}:59): {_n(top['pedidos'])} pedidos, el "
        f"{_pct(top['pedidos'] / total)} de los {_n(total)} pedidos.",
    ]
    if len(filas) > 1:
        siguen = "; ".join(f"{i}. hora {f['order_hour_of_day']} ({_n(f['pedidos'])})"
                           for i, f in enumerate(filas[1:5], 2))
        hallazgos.append(f"Le siguen: {siguen}.")
    return hallazgos, [
        _vc("Hora con más pedidos", top["order_hour_of_day"], "hora (0-23)"),
        _vc("Pedidos en esa hora", top["pedidos"], "pedidos"),
    ]


def _redactar_n9(filas: list[dict], params: dict) -> tuple[list[str], list[ValorClave]]:
    if not filas:
        return _sin_filas("par de productos")
    top = filas[0]
    hallazgos = [
        f"Los dos productos que más se compran juntos son {top['producto_a']} y {top['producto_b']}: aparecen "
        f"en el mismo pedido {_n(top['pedidos_juntos'])} veces.",
    ]
    if len(filas) > 1:
        siguen = "; ".join(f"{i}. {f['producto_a']} + {f['producto_b']} ({_n(f['pedidos_juntos'])})"
                           for i, f in enumerate(filas[1:5], 2))
        hallazgos.append(f"Le siguen: {siguen}.")
    minimo, ultimo = params["min_pedidos_producto"], filas[-1]["pedidos_juntos"]
    if len(filas) == params["top_n"] and ultimo >= minimo:
        hallazgos.append(
            f"El par número {len(filas)} se compra junto {_n(ultimo)} veces, al menos el umbral de {_n(minimo)} "
            "pedidos por producto: un par con más co-ocurrencias necesita que cada producto esté en al menos "
            "esos pedidos, así que dejar fuera los productos con menos pedidos no cambia este top."
        )
    else:
        hallazgos.append(
            f"Con estos datos no se puede asegurar que el umbral de {_n(minimo)} pedidos por producto no deje "
            "fuera algún par: el último par del top tiene menos co-ocurrencias que el umbral."
        )
    return hallazgos, [
        _vc("Par que más se compra junto", f"{top['producto_a']} + {top['producto_b']}", "par de productos"),
        _vc("Pedidos con ese par", top["pedidos_juntos"], "pedidos"),
    ]


def _redactar_n10(filas: list[dict], _params: dict) -> tuple[list[str], list[ValorClave]]:
    f = filas[0] if filas else {}
    if not f.get("pedidos"):
        return ["No hay pedidos con productos en estos datos."], []
    mezcla = 100.0 - f["pct_pedidos_todo_recomprado"] - f["pct_pedidos_todo_nuevo"]
    return [
        f"El {f['pct_pedidos_todo_recomprado']:.2f}% de los pedidos son 100% de productos recomprados "
        f"({_n(f['pedidos_todo_recomprado'])} pedidos «piloto automático») y el "
        f"{f['pct_pedidos_todo_nuevo']:.2f}% son 100% productos nuevos ({_n(f['pedidos_todo_nuevo'])} pedidos "
        f"«exploratorios»), de {_n(f['pedidos'])} pedidos con productos.",
        f"El {mezcla:.2f}% restante mezcla productos recomprados y nuevos.",
    ], [
        _vc("% pedidos 100% recomprados", f["pct_pedidos_todo_recomprado"], "%"),
        _vc("% pedidos 100% nuevos", f["pct_pedidos_todo_nuevo"], "%"),
        _vc("Pedidos con productos", f["pedidos"], "pedidos"),
    ]


_P_TOP_N = ("top_n", TOP_N_DEFAULT, "La pregunta pide los 10 primeros.")
_P_TOP_N_CONTEXTO = ("top_n", TOP_N_DEFAULT,
                     "La pregunta pide uno solo (la primera fila); las 10 primeras se guardan como contexto "
                     "para el dashboard.")
_P_TOP_N_CONVENCION = ("top_n", TOP_N_DEFAULT,
                       "La pregunta no fija cuántos («¿cuáles...?»): la referencia guarda los 10 primeros por "
                       "convención, y el chequeo automático compara los primeros nombres del intento contra ellos.")
_P_MIN_PEDIDOS = ("min_pedidos", MIN_PEDIDOS_CLIENTE_DEFAULT,
                  "Deja fuera a los clientes con pocos pedidos, cuyo promedio de días sale de 1 o 2 intervalos. "
                  "Es la mediana de pedidos por cliente (10). No cambia el top: los primeros tienen 100 pedidos.")
_P_MIN_PEDIDOS_PRODUCTO = (
    "min_pedidos_producto", MIN_PEDIDOS_PRODUCTO_PAR_DEFAULT,
    "Un par que se compra junto K veces exige que cada producto esté en al menos K pedidos. Considerar solo los "
    "productos con al menos 10,000 pedidos es exacto mientras el par número 10 tenga 10,000 o más "
    "co-ocurrencias (con los datos reales tiene 42,283, y con 5,000 el top sale idéntico). Sin el umbral, el "
    "self-join sobre 33.8M líneas se queda sin memoria con el límite de 4 GB del proyecto (medido: 79 s y "
    "Out of Memory).")
_P_MIN_COMPRAS = ("min_compras", MIN_COMPRAS_DEFAULT,
                  "Lo fija la pregunta («al menos 50 compras»): con menos compras, una tasa de 1/1 o 0/1 no "
                  "significa nada. Es el mismo umbral que ya usaba REGLAS_DE_NEGOCIO.")

DEFINICIONES: dict[str, DefNecesidad] = {d.id: d for d in (
    DefNecesidad(
        id="N1", titulo="Clientes de compra más sostenida",
        pregunta=("¿Quiénes son los clientes con la frecuencia de compra más sostenida (más pedidos totales y "
                  "menor promedio de días entre pedidos consecutivos)?"),
        decision="A qué clientes dirigir una oferta de fidelización o retención.",
        sql_plantilla="""
SELECT user_id,
       COUNT(order_id) AS total_pedidos,
       AVG(days_since_prior_order) AS promedio_dias_entre_pedidos
FROM orders
GROUP BY user_id
HAVING COUNT(order_id) >= {min_pedidos}
ORDER BY total_pedidos DESC, promedio_dias_entre_pedidos ASC, user_id
LIMIT {top_n}""",
        universo="Todos los pedidos (orders, prior + train + test), agrupados por cliente (user_id).",
        filtros=("clientes con al menos min_pedidos pedidos",),
        limitaciones=(
            "El dataset guarda como máximo 100 pedidos por cliente: 1,374 clientes llegan a ese tope, así que "
            "entre los primeros el orden lo decide el promedio de días, no el total de pedidos.",
            "days_since_prior_order está censurado en 30 y es NULL en el primer pedido (AVG lo ignora). Un "
            "promedio menor a 1 día significa varios pedidos el mismo día: puede ser una cuenta de negocio o "
            "un patrón atípico más que un cliente fiel.",
            "user_id es anónimo: identifica al cliente, no dice quién es.",
        ),
        criterio_corte="Los top_n clientes por total de pedidos (desc) y promedio de días (asc), desempate por user_id",
        tipo=LISTA, columna_nombre="user_id", columna_valor="promedio_dias_entre_pedidos", entidad="cliente",
        filas_esperadas=None, redactar=_redactar_n1, parametros=(_P_MIN_PEDIDOS, _P_TOP_N_CONVENCION),
        nombre_es_texto=False,
    ),
    DefNecesidad(
        id="N2", titulo="Departamento con mayor tasa de recompra",
        pregunta="¿Qué departamento tiene la tasa de recompra promedio más alta?",
        decision="En qué departamento la clientela ya es fiel (y dónde hay que trabajar la recompra).",
        sql_plantilla="""
SELECT d.department,
       COUNT(*) AS veces_comprado,
       SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) AS recompras,
       SUM(CASE WHEN op.reordered = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS tasa_recompra
FROM order_products op
JOIN products p ON op.product_id = p.product_id
JOIN departments d ON p.department_id = d.department_id
GROUP BY d.department
ORDER BY tasa_recompra DESC, d.department
LIMIT {top_n}""",
        universo=_UNIVERSO_LINEAS + " Cada línea cuenta en el departamento de su producto.",
        filtros=("ninguno: todas las líneas prior + train",),
        limitaciones=(
            "Tasa de recompra de un departamento = líneas con reordered = 1 / líneas del departamento (el "
            "«promedio» de reordered sobre sus líneas). No es el promedio de las tasas de sus productos: un "
            "producto que se vende mucho pesa más.",
            "reordered vale 0 en la primera compra de cada usuario, así que los departamentos que se prueban "
            "una vez tienen tasas más bajas aunque se vendan mucho.",
            "Incluye los departamentos «missing» y «other», que agrupan productos sin clasificar.",
        ),
        criterio_corte="Los top_n departamentos por tasa de recompra (desc), desempate por nombre",
        tipo=UNICO, columna_nombre="department", columna_valor="tasa_recompra", entidad="departamento",
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
        id="N8", titulo="Hora del día con más pedidos",
        pregunta="¿En qué franja horaria del día se concentra el mayor volumen de pedidos?",
        decision="En qué horas reforzar personal, repartidores y capacidad de la app.",
        sql_plantilla="""
SELECT order_hour_of_day, COUNT(*) AS pedidos
FROM orders
GROUP BY order_hour_of_day
ORDER BY pedidos DESC, order_hour_of_day""",
        universo="Todos los pedidos (orders, prior + train + test), por la hora del día en que se hicieron.",
        filtros=("ninguno",),
        limitaciones=(
            "La franja es la hora entera de order_hour_of_day (0-23). Es una hora anónima: no se sabe de qué "
            "zona horaria es, así que «la hora 10» es relativa al dataset, no a un reloj real.",
            "Cuenta pedidos (incluidos los eval_set = 'test', que tienen hora aunque no tengan productos), no "
            "productos ni unidades.",
        ),
        criterio_corte="Las 24 horas por número de pedidos (desc), desempate por hora",
        tipo=UNICO, columna_nombre="order_hour_of_day", columna_valor="pedidos", entidad="hora del día",
        filas_esperadas=1, redactar=_redactar_n8, nombre_es_texto=False,
    ),
    DefNecesidad(
        id="N9", titulo="Productos que se compran juntos",
        pregunta="¿Cuáles son los dos productos que se compran juntos con más frecuencia (candidatos a combo/oferta)?",
        decision="Qué par de productos ofrecer como combo u oferta conjunta.",
        sql_plantilla="""
SELECT pa.product_name AS producto_a, pb.product_name AS producto_b, pares.pedidos_juntos
FROM (
    SELECT a.product_id AS id_a, b.product_id AS id_b, COUNT(*) AS pedidos_juntos
    FROM order_products a
    JOIN order_products b ON a.order_id = b.order_id AND a.product_id < b.product_id
    WHERE a.product_id IN (SELECT product_id FROM order_products GROUP BY product_id HAVING COUNT(*) >= {min_pedidos_producto})
      AND b.product_id IN (SELECT product_id FROM order_products GROUP BY product_id HAVING COUNT(*) >= {min_pedidos_producto})
    GROUP BY a.product_id, b.product_id
) pares
JOIN products pa ON pares.id_a = pa.product_id
JOIN products pb ON pares.id_b = pb.product_id
ORDER BY pares.pedidos_juntos DESC, producto_a, producto_b
LIMIT {top_n}""",
        universo=(_UNIVERSO_LINEAS + " Pares de productos distintos dentro del mismo pedido (self-join por "
                  "order_id, product_id_a < product_id_b para no contar el par dos veces)."),
        filtros=("productos con al menos min_pedidos_producto pedidos",),
        limitaciones=(
            "Mide co-ocurrencia por volumen, no afinidad: los productos más vendidos (plátanos, fresas, "
            "aguacates) aparecen juntos solo por ser populares. Un par «raro pero fuerte» necesitaría otra "
            "métrica (lift), que es otra pregunta.",
            "Cada par se cuenta una vez por pedido. El orden de los dos nombres sigue el product_id y no "
            "significa nada.",
            "Es una sola consulta, pero pesada: el self-join genera cientos de millones de pares. Por eso, "
            "cuando un modelo la genera, su límite de ejecución es de 25 s, no los 10 s del chat.",
            "El dataset no tiene precios ni márgenes: el par sale solo por volumen, sin saber si conviene "
            "como combo u oferta.",
        ),
        criterio_corte="Los top_n pares por pedidos juntos (desc), desempate por nombres",
        tipo=UNICO, columna_nombre="producto_a", columna_valor="pedidos_juntos", entidad="par de productos",
        filas_esperadas=1, redactar=_redactar_n9, parametros=(_P_MIN_PEDIDOS_PRODUCTO, _P_TOP_N_CONTEXTO),
        columnas_nombre_extra=("producto_b",),
        # Fase 29: con la conexión de la guarda (12 hilos), la referencia tarda 1.4 s, pero el
        # self-join más directo que escribe un modelo (agrupando por ids, sin podar) tarda 10.4 s.
        timeout_consulta_s=25.0,
    ),
    DefNecesidad(
        id="N10", titulo="Pedidos 100% recomprados vs. 100% nuevos",
        pregunta=("¿Qué porcentaje de los pedidos son 100% de productos recomprados frente a los que son 100% "
                  "productos nuevos?"),
        decision="Cuánto del negocio es reposición automática y cuánto es exploración (dónde recomendar).",
        sql_plantilla="""
SELECT SUM(CASE WHEN recompradas = lineas THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS pct_pedidos_todo_recomprado,
       SUM(CASE WHEN recompradas = 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS pct_pedidos_todo_nuevo,
       SUM(CASE WHEN recompradas = lineas THEN 1 ELSE 0 END) AS pedidos_todo_recomprado,
       SUM(CASE WHEN recompradas = 0 THEN 1 ELSE 0 END) AS pedidos_todo_nuevo,
       COUNT(*) AS pedidos
FROM (
    SELECT order_id, COUNT(*) AS lineas, SUM(CASE WHEN reordered = 1 THEN 1 ELSE 0 END) AS recompradas
    FROM order_products
    GROUP BY order_id
) t""",
        universo="Pedidos con productos (prior + train): por pedido, cuántas de sus líneas son recompras.",
        filtros=("se excluyen los pedidos eval_set = 'test' (no tienen productos)",),
        limitaciones=(
            "El primer pedido de cada cliente es 100% nuevo por definición (reordered = 0 en todas sus "
            "líneas): parte del porcentaje de pedidos «exploratorios» son primeros pedidos.",
            "Un pedido de un solo producto cae siempre en una de las dos categorías.",
        ),
        criterio_corte="Una fila",
        tipo=ESCALAR, columna_nombre=None, columna_valor="pct_pedidos_todo_recomprado", entidad=None,
        filas_esperadas=1, redactar=_redactar_n10, columnas_valor_extra=("pct_pedidos_todo_nuevo",),
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
    claves = (d.columna_nombre, *d.columnas_nombre_extra, d.columna_valor, *d.columnas_valor_extra)
    primeras = [c for c in claves if c and c in filas[0]]
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
