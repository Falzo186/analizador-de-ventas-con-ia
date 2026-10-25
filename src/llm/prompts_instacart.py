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


def _sistema_sql(descripcion_esquema: str, lecciones: str | None, ejemplos: str | None = None) -> str:
    """Prompt de sistema de la llamada de SQL. `lecciones` (Fase 27): el
    bloque de la bitácora del chat (`bitacora_chat.Bitacora.bloque_prompt`),
    al final, después de las reglas de negocio. `ejemplos` (Fase 30): el
    bloque few-shot (`few_shot.contexto_few_shot`), después del esquema y las
    reglas y ANTES de las lecciones. Sin lecciones ni ejemplos (técnica base),
    el prompt es idéntico al de antes."""
    sistema = _PLANTILLA_SQL.format(descripcion_esquema=descripcion_esquema)
    if ejemplos:
        sistema = f"{sistema}\n{ejemplos.strip()}\n"
    return f"{sistema}\n{lecciones.strip()}\n" if lecciones else sistema


def prompt_sql_instacart(
    pregunta: str, descripcion_esquema: str, contexto: list[TurnoPrevio] | None = None,
    retroalimentacion: str | None = None, lecciones: str | None = None, ejemplos: str | None = None,
) -> tuple[str, str]:
    """Devuelve (mensaje_de_sistema, mensaje_de_usuario) para la llamada 1
    (texto -> SQL). `descripcion_esquema` viene de
    `src.llm.esquema_instacart.construir_descripcion_esquema()` -- este
    módulo nunca escribe nombres de tabla/columna por su cuenta.
    `contexto`: turnos previos (Fase 6), ver `_con_contexto`.
    `retroalimentacion`: intento anterior (Fase 23), ver `_con_retroalimentacion`.
    `lecciones`: bitácora del chat (Fase 27); `ejemplos`: few-shot (Fase 30), ver `_sistema_sql`."""
    sistema = _sistema_sql(descripcion_esquema, lecciones, ejemplos)
    return sistema, _con_retroalimentacion(_con_contexto(pregunta, contexto), retroalimentacion, _CIERRE_SQL_RETRO)


def prompt_sql_reintento_instacart(
    pregunta: str, descripcion_esquema: str, sql_anterior: str, motivo: str,
    contexto: list[TurnoPrevio] | None = None, retroalimentacion: str | None = None,
    lecciones: str | None = None, ejemplos: str | None = None,
) -> tuple[str, str]:
    """Igual que `prompt_sql_instacart`, para el ÚNICO reintento: mismo
    sistema, pero el mensaje de usuario agrega el SQL rechazado y el motivo
    EXACTO del rechazo (el de `validar_sql()`, el error del motor o, desde la
    Fase 27, el del revisor de SQL)."""
    sistema = _sistema_sql(descripcion_esquema, lecciones, ejemplos)
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
Eres un evaluador estricto. Comparas el INTENTO de un asistente (su SQL, el
resultado de ese SQL y su respuesta redactada) con la REFERENCIA, que es
correcta: un programa determinista ejecutó el SQL de referencia sobre los
mismos datos (dataset Instacart). Calificas el CONTENIDO (datos y
conclusiones), no la redacción ni el estilo.

- "correcta": responde lo que se pregunta y sus datos principales coinciden
  con la referencia (se aceptan cifras redondeadas y empates en otro orden).
- "parcialmente correcta": acierta en algo (algún dato coincide con la
  referencia), pero responde solo una parte, usa otra métrica o mezcla
  aciertos con errores.
- "incorrecta": no responde la pregunta, sus datos no coinciden con la
  referencia, o dice que no hay datos cuando la referencia sí los tiene.

No uses conocimiento propio: solo la referencia decide qué es correcto.

Escribe DOS textos separados:
1. "comparacion": en qué coincide o difiere el resultado del intento con el
   de la referencia. Aquí SÍ puedes citar valores, nombres y cifras de la
   referencia. Este texto NUNCA se le muestra al asistente.
2. "diagnostico_enfoque": qué está mal en el ENFOQUE del intento, para que el
   asistente lo corrija en su próximo intento SIN ver la respuesta correcta.
   Señala la causa concreta: tabla o JOIN que falta o sobra (p. ej. "falta
   el JOIN con products para traer el nombre"), columna equivocada,
   agrupación, filtro, métrica (p. ej. "cuentas líneas en vez de pedidos
   distintos", "te quedaste con el conteo absoluto en vez de dividir entre
   el total de veces comprado"), orden, LIMIT, o un problema de la
   redacción. PROHIBIDO en este texto: cualquier cifra, nombre de producto,
   pasillo o departamento, o valor del resultado de la referencia, y copiar
   el SQL de referencia. Si el intento es correcto, escribe "".

Responde ÚNICAMENTE con un objeto JSON, sin texto adicional, con las claves en este orden:
{"comparacion": "<una o dos frases>",
 "veredicto": "correcta" | "parcialmente correcta" | "incorrecta",
 "diagnostico_enfoque": "<una o dos frases, sin valores de la referencia>"}
"""


def prompt_juez_instacart(
    pregunta: str, referencia: str, respuesta: str, *, sql_generado: str | None = None,
    resultado_generado: str | None = None,
) -> tuple[str, str]:
    """(sistema, usuario) del juez (`src.llm.iteraciones_juez`): la pregunta,
    la referencia (SQL + resultado + redacción del software determinista) y
    el intento: SQL generado, su resultado y la respuesta redactada.
    Fase 24: el juez ve también el SQL y el resultado del intento, para poder
    diagnosticar el ENFOQUE y no solo comparar la redacción."""
    intento = []
    if sql_generado is not None:
        intento.append(f"SQL del intento:\n{' '.join(sql_generado.split())}")
    if resultado_generado is not None:
        intento.append(f"Resultado de ese SQL:\n{resultado_generado.strip()}")
    intento.append(f"Respuesta redactada:\n{respuesta.strip()}")
    usuario = (
        f"PREGUNTA:\n{pregunta.strip()}\n\n"
        f"REFERENCIA (correcta):\n{referencia.strip()}\n\n"
        "INTENTO DEL ASISTENTE (a evaluar):\n" + "\n\n".join(intento)
    )
    return _SISTEMA_JUEZ, usuario


#: Fase 25: el juez ya NO decide si el intento está bien o mal (lo decide el
#: chequeo automático contra la referencia, `src.llm.retroalimentacion`);
#: solo se le llama cuando el intento ya falló, para que explique POR QUÉ.
#: A propósito sin frases de ejemplo literales: en la Fase 24 el juez copió
#: tal cual un ejemplo de su prompt como diagnóstico de un caso al que no
#: aplicaba.
_SISTEMA_DIAGNOSTICO = """\
Eres un evaluador. Un chequeo automático YA DETERMINÓ que el INTENTO de un
asistente es INCORRECTO: el resultado de su SQL no coincide con el de la
REFERENCIA, que es correcta (un programa determinista ejecutó el SQL de
referencia sobre los mismos datos del dataset Instacart). No tienes que
decidir si está bien o mal: está mal. Tu trabajo es explicar POR QUÉ, para
que el asistente lo corrija en su próximo intento.

Compara el SQL del intento con el SQL de referencia y su resultado con el de
la referencia, y encuentra la diferencia de ENFOQUE que causa el error: una
tabla o un JOIN que falta o sobra, una columna equivocada, un filtro de más
o de menos, una agrupación distinta, otra métrica (otro conteo, otro
promedio, otra división), otro orden, o un LIMIT. Sé concreto y nombra las
columnas y tablas involucradas. No inventes errores: si una parte del
intento ya está igual que en la referencia, no la critiques.

Escribe DOS textos separados:
1. "comparacion": qué diferencia hay entre el resultado del intento y el de
   la referencia. Aquí SÍ puedes citar valores. NUNCA se le muestra al
   asistente.
2. "diagnostico_enfoque": la causa del error en una o dos frases, dirigida
   al asistente. PROHIBIDO en este texto: cualquier cifra, nombre de
   producto, pasillo o departamento, o valor del resultado de la referencia,
   y copiar el SQL de referencia completo.

Responde ÚNICAMENTE con un objeto JSON, sin texto adicional:
{"comparacion": "<una o dos frases>", "diagnostico_enfoque": "<una o dos frases, sin valores de la referencia>"}
"""


def prompt_diagnostico_instacart(
    pregunta: str, referencia: str, respuesta: str, *, sql_generado: str | None, resultado_generado: str | None,
    detalle_chequeo: str,
) -> tuple[str, str]:
    """(sistema, usuario) del diagnóstico de la Fase 25: el mismo material que
    `prompt_juez_instacart` más lo que el chequeo automático encontró que no
    coincide (`detalle_chequeo`; puede citar la referencia: el juez ya la ve)."""
    _, usuario = prompt_juez_instacart(pregunta, referencia, respuesta, sql_generado=sql_generado,
                                       resultado_generado=resultado_generado)
    usuario += f"\n\nCHEQUEO AUTOMÁTICO (ya decidido: INCORRECTO): {detalle_chequeo.strip()}"
    return _SISTEMA_DIAGNOSTICO, usuario
