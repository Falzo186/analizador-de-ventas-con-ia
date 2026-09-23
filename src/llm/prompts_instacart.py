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


def prompt_sql_instacart(pregunta: str, descripcion_esquema: str) -> tuple[str, str]:
    """Devuelve (mensaje_de_sistema, mensaje_de_usuario) para la llamada 1
    (texto -> SQL). `descripcion_esquema` viene de
    `src.llm.esquema_instacart.construir_descripcion_esquema()` -- este
    módulo nunca escribe nombres de tabla/columna por su cuenta."""
    sistema = _PLANTILLA_SQL.format(descripcion_esquema=descripcion_esquema)
    return sistema, pregunta.strip()


def prompt_sql_reintento_instacart(
    pregunta: str, descripcion_esquema: str, sql_anterior: str, motivo: str,
) -> tuple[str, str]:
    """Igual que `prompt_sql_instacart`, para el ÚNICO reintento: mismo
    sistema, pero el mensaje de usuario agrega el SQL rechazado y el motivo
    EXACTO del rechazo (el de `validar_sql()` o el error del motor)."""
    sistema = _PLANTILLA_SQL.format(descripcion_esquema=descripcion_esquema)
    usuario = (
        f"{pregunta.strip()}\n\n"
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


def prompt_respuesta_instacart(pregunta: str, tabla_resultado: str) -> tuple[str, str]:
    """Devuelve (sistema, usuario) para la llamada 2 (resultado -> texto)."""
    usuario = (
        f"Pregunta del usuario:\n{pregunta.strip()}\n\n"
        f"Resultado de la consulta:\n{tabla_resultado}"
    )
    return _SISTEMA_RESPUESTA, usuario
