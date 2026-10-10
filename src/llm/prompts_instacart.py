"""Plantillas de prompt para la ruta Instacart. Misma FORMA que
`src/llm/prompts.py` (dos funciones, cada una devuelve `(sistema, usuario)`)
para que `nl2sql_instacart.py`/`responder_instacart.py` las usen igual que
sus equivalentes de Northwind -- pero el CONTENIDO es distinto: sin fechas
inventadas, esquema dinámico recibido como parámetro (no escrito aquí), y
las reglas de negocio de Instacart en vez de las de Northwind.

Fase 5, decisión ya tomada: ruta nueva y paralela. Este archivo no importa
nada de `src/llm/prompts.py` ni lo modifica.
"""

from __future__ import annotations

_PLANTILLA_SQL = """\
Eres un traductor de preguntas en español a consultas SQL para DuckDB.
Trabajas sobre el dataset INSTACART (Instacart Online Grocery Shopping
Dataset 2017): pedidos reales de supermercado en línea, con productos,
pasillos y departamentos. NO es una tienda simulada ni tiene relación con
Northwind Traders: ignora cualquier nombre de tabla que conozcas de otras
bases de datos (Products con mayúscula inicial, Orders, "Order Details",
Customers, Employees, Suppliers, Categories -- NADA de eso existe aquí).

{descripcion_esquema}

REGLAS (obligatorias)
- Responde ÚNICAMENTE con UNA sentencia SELECT válida para DuckDB.
- PROHIBIDO: INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH, PRAGMA,
  o cualquier otra cosa que no sea leer datos.
- Usa EXCLUSIVAMENTE las tablas, la vista y las columnas listadas arriba
  en "ESQUEMA REAL". Si la pregunta menciona algo que no está en ese
  esquema (un precio, un cliente, un empleado, una fecha de calendario),
  NO inventes una columna ni una tabla: genera igual una consulta SELECT
  que sea válida y deje claro en sus propias columnas/alias que ese dato
  no existe (por ejemplo, seleccionando NULL con un alias descriptivo como
  "sin_precio_en_el_dataset"), en vez de fallar sin generar nada.
- Nada de explicaciones, comentarios ni markdown. Sin ```sql, sin backticks.
- No asumas ni calcules una fecha de calendario ni "hoy": este dataset no
  tiene fechas reales (ver ESQUEMA REAL arriba). Si la pregunta pide algo
  temporal en términos de calendario ("el mes pasado", "esta semana"),
  usa las columnas relativas que SÍ existen (order_number,
  days_since_prior_order) y dejá claro con el alias que es relativo, no
  una fecha real.
"""


#: Turno previo de la conversación que se ofrece como contexto: (pregunta,
#: SQL que la respondió). Solo pregunta + SQL, nunca las filas.
TurnoPrevio = tuple[str, str]


def _con_contexto(pregunta: str, contexto: list[TurnoPrevio] | None) -> str:
    """Mensaje de usuario con los turnos previos como contexto de
    seguimiento. Sin contexto devuelve la pregunta tal cual (el prompt de
    una pregunta suelta no cambia)."""
    if not contexto:
        return pregunta.strip()
    lineas = [
        "Conversación previa (solo para entender preguntas de seguimiento "
        "como \"¿y de tipo test?\"; NO la respondas de nuevo):",
    ]
    for pregunta_previa, sql_previo in contexto:
        lineas.append(f"- Pregunta: {pregunta_previa.strip()}")
        lineas.append(f"  SQL: {' '.join(sql_previo.split())}")
    lineas.append("")
    lineas.append(f"Pregunta actual (genera el SQL SOLO para esta): {pregunta.strip()}")
    return "\n".join(lineas)


def _con_retroalimentacion(usuario: str, retroalimentacion: str | None, cierre: str) -> str:
    """Fase 23 (iteraciones en modo autocorrección): agrega al mensaje de
    usuario lo que pasó en el intento anterior de la misma pregunta. Sin
    retroalimentación devuelve `usuario` tal cual (el chat no cambia)."""
    if not retroalimentacion:
        return usuario
    return f"{usuario}\n\n{retroalimentacion.strip()}\n\n{cierre}"


_CIERRE_SQL_RETRO = (
    "Genera ahora el SQL para la pregunta corrigiendo lo que falló en el intento "
    "anterior (si fue correcto, conserva el enfoque)."
)
_CIERRE_RESPUESTA_RETRO = (
    "Redacta ahora la respuesta con el resultado de ESTA consulta (no el del intento "
    "anterior), evitando el error que se señaló."
)


def prompt_sql_instacart(
    pregunta: str, descripcion_esquema: str, contexto: list[TurnoPrevio] | None = None,
    retroalimentacion: str | None = None,
) -> tuple[str, str]:
    """Devuelve (mensaje_de_sistema, mensaje_de_usuario) para la llamada 1
    (texto -> SQL). `descripcion_esquema` viene de
    `src.llm.esquema_instacart.construir_descripcion_esquema()` -- este
    módulo nunca escribe nombres de tabla/columna por su cuenta.
    `contexto`: turnos previos (Fase 6), ver `_con_contexto`.
    `retroalimentacion`: intento anterior (Fase 23), ver `_con_retroalimentacion`."""
    sistema = _PLANTILLA_SQL.format(descripcion_esquema=descripcion_esquema)
    return sistema, _con_retroalimentacion(_con_contexto(pregunta, contexto), retroalimentacion, _CIERRE_SQL_RETRO)


def prompt_sql_reintento_instacart(
    pregunta: str, descripcion_esquema: str, sql_anterior: str, motivo: str,
    contexto: list[TurnoPrevio] | None = None, retroalimentacion: str | None = None,
) -> tuple[str, str]:
    """Igual que `prompt_sql_instacart`, para el ÚNICO reintento: mismo
    sistema, pero el mensaje de usuario agrega el SQL rechazado y el motivo
    EXACTO del rechazo (el de `validar_sql()` o el error del motor)."""
    sistema = _PLANTILLA_SQL.format(descripcion_esquema=descripcion_esquema)
    usuario = (
        f"{_con_retroalimentacion(_con_contexto(pregunta, contexto), retroalimentacion, _CIERRE_SQL_RETRO)}\n\n"
        f"Tu consulta anterior fue:\n{sql_anterior.strip()}\n\n"
        f"Tu consulta anterior fue rechazada porque: {motivo.strip()}\n"
        "Genera solo una sentencia SELECT válida sobre las tablas reales."
    )
    return sistema, usuario


_SISTEMA_RESPUESTA = """\
Redactas respuestas breves y claras en español para un usuario de negocio.
Recibes su pregunta original y el resultado ya calculado de una consulta al
dataset Instacart (pedidos de supermercado en línea: productos, pasillos,
departamentos -- SIN precios ni datos monetarios de ningún tipo).

- Contesta en 1 o 2 frases, en español natural.
- Usa el número EXACTO que aparece en el resultado; no lo redondees ni lo cambies.
- Si el resultado está vacío, es 0 o es NULL, dilo con naturalidad
  ("no hay pedidos en esa situación...").
- No inventes datos que no estén en el resultado.
- Si la pregunta pedía una cifra de dinero (ingreso, ganancia, precio,
  costo) y el resultado no la trae, dilo explícitamente: este dataset no
  tiene precios ni ningún dato monetario, así que esa cifra no se puede
  calcular. No inventes un monto ni aunque el usuario insista.
- No menciones SQL, consultas, tablas ni bases de datos.
"""


def prompt_respuesta_instacart(
    pregunta: str, tabla_resultado: str, retroalimentacion: str | None = None,
) -> tuple[str, str]:
    """Devuelve (sistema, usuario) para la llamada 2 (resultado -> texto).
    `retroalimentacion`: intento anterior (Fase 23, modo autocorrección)."""
    usuario = (
        f"Pregunta del usuario:\n{pregunta.strip()}\n\n"
        f"Resultado de la consulta:\n{tabla_resultado}"
    )
    return _SISTEMA_RESPUESTA, _con_retroalimentacion(usuario, retroalimentacion, _CIERRE_RESPUESTA_RETRO)


def prompt_respuesta_estricta_instacart(
    pregunta: str, tabla_resultado: str, cifras_alteradas: list[str], valores_exactos: list[str],
) -> tuple[str, str]:
    """Reintento ÚNICO de la llamada 2 cuando la redacción anterior traía
    cifras que no están en el resultado (`src.llm.cifras_instacart`)."""
    sistema = _SISTEMA_RESPUESTA + (
        "- REGLA ESTRICTA: copia cada cifra tal cual aparece en la lista de "
        "valores exactos, carácter por carácter. No agregues, quites ni "
        "muevas dígitos ni separadores. Si dudas, escribe el número sin separadores.\n"
    )
    usuario = (
        f"Pregunta del usuario:\n{pregunta.strip()}\n\n"
        f"Resultado de la consulta:\n{tabla_resultado}\n\n"
        f"Tu respuesta anterior tenía cifras que NO están en el resultado: {', '.join(cifras_alteradas)}.\n"
        f"Valores exactos que puedes usar: {', '.join(valores_exactos)}."
    )
    return sistema, usuario


#: Fase 11: veredictos posibles del juez, en el texto exacto que se le pide.
VEREDICTOS_JUEZ: tuple[str, ...] = ("correcta", "parcialmente correcta", "incorrecta")

_SISTEMA_JUEZ = """\
Eres un evaluador estricto. Comparas la RESPUESTA GENERADA por un asistente
con la RESPUESTA DE REFERENCIA, que es correcta: la calculó un programa
determinista sobre los mismos datos (dataset Instacart). Calificas qué tan
apegada está la respuesta generada a la referencia en CONTENIDO (datos y
conclusiones), no en redacción ni en estilo.

- "correcta": responde lo que se pregunta y sus datos o conclusiones
  principales coinciden con la referencia (se aceptan cifras redondeadas).
- "parcialmente correcta": acierta en algo (alguna cifra o conclusión
  coincide con la referencia), pero responde solo una parte de la pregunta,
  omite lo principal o mezcla aciertos con errores.
- "incorrecta": no responde la pregunta, contradice la referencia, sus
  cifras no coinciden con ninguna de la referencia, o dice que no hay datos
  cuando la referencia sí los tiene.

La referencia es más detallada que una respuesta breve: no exijas que repita
todo, pero sí lo esencial para la pregunta. No uses conocimiento propio:
solo la referencia decide qué es correcto.

Responde ÚNICAMENTE con un objeto JSON, sin texto adicional:
{"veredicto": "correcta" | "parcialmente correcta" | "incorrecta",
 "justificacion": "<una o dos frases: en qué se parece a la referencia o en qué falló>"}
"""


def prompt_juez_instacart(pregunta: str, referencia: str, respuesta: str) -> tuple[str, str]:
    """(sistema, usuario) del juez de la Fase 11 (`src.llm.iteraciones_juez`):
    la pregunta, la respuesta de referencia (del software determinista) y
    la respuesta generada en una iteración."""
    usuario = (
        f"PREGUNTA:\n{pregunta.strip()}\n\n"
        f"RESPUESTA DE REFERENCIA (correcta):\n{referencia.strip()}\n\n"
        f"RESPUESTA GENERADA (a evaluar):\n{respuesta.strip()}"
    )
    return _SISTEMA_JUEZ, usuario
