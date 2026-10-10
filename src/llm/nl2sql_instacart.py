"""Llamada 1 de la ruta Instacart: pregunta en español -> sentencia SELECT.

No ejecuta nada contra la base de datos ni valida seguridad, solo produce
el texto SQL. `limpiar_sql()` (limpieza de texto genérica: fences de
markdown, prefijo "sql", texto antes del SELECT) venía de `src.llm.nl2sql`
(ruta Northwind); en la Fase 7, al retirar Northwind a `legacy/`, se
trasladó aquí sin cambios.

`modelo` es opcional (default: `client.modelo_actual_instacart()`) para que
el benchmark pueda probar varios modelos por el mismo camino que el chat.
"""

from __future__ import annotations

import re

from src.llm import client
from src.llm.prompts_instacart import TurnoPrevio, prompt_sql_instacart, prompt_sql_reintento_instacart

__all__ = ["limpiar_sql", "generar_sql_instacart", "generar_sql_reintento_instacart"]

# Bloque markdown completo:  ```sql\n ... \n```   o   ```\n ... \n```
_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", re.DOTALL)


def limpiar_sql(texto: str) -> str:
    """Quita adornos que suelen añadir los modelos: fences, backticks, prefijo
    'sql', texto introductorio antes del SELECT y el punto y coma final.
    """
    t = (texto or "").strip()

    m = _FENCE.match(t)
    if m:
        t = m.group(1).strip()
    else:
        t = t.strip("`").strip()

    # Prefijo "sql" que algunos modelos ponen como etiqueta.
    if t[:4].lower() in ("sql\n", "sql "):
        t = t[3:].strip()

    # Si el modelo escribió algo antes del SELECT ("Aquí está la consulta:"),
    # nos quedamos desde el primer SELECT.
    m = re.search(r"(?is)\bselect\b", t)
    if m and m.start() > 0:
        t = t[m.start():].strip()

    return t.rstrip(";").strip()


def generar_sql_instacart(
    pregunta: str,
    descripcion_esquema: str,
    *,
    modelo: str | None = None,
    timeout_s: float | None = None,
    contexto: list[TurnoPrevio] | None = None,
    temperatura: float = 0.0,
    retroalimentacion: str | None = None,
    lecciones: str | None = None,
) -> str:
    """Pide al modelo (Instacart, `client.modelo_actual_instacart()`) el
    SQL para `pregunta`, dado el esquema ya introspeccionado, y lo
    devuelve limpio. `contexto`: turnos previos de la conversación.
    `temperatura`: 0.0 en el chat; solo el modo de iteraciones de la
    Fase 11 (`src.llm.iteraciones_juez`) pasa un valor mayor.
    `retroalimentacion`: intento anterior (Fase 23, modo autocorrección)."""
    sistema, usuario = prompt_sql_instacart(pregunta, descripcion_esquema, contexto, retroalimentacion, lecciones)
    crudo = client.chat_con_modelo(
        modelo or client.modelo_actual_instacart(), sistema, usuario, timeout_s=timeout_s,
        temperatura=temperatura,
    )
    return limpiar_sql(crudo)


def generar_sql_reintento_instacart(
    pregunta: str,
    descripcion_esquema: str,
    sql_anterior: str,
    motivo: str,
    *,
    modelo: str | None = None,
    timeout_s: float | None = None,
    contexto: list[TurnoPrevio] | None = None,
    temperatura: float = 0.0,
    retroalimentacion: str | None = None,
    lecciones: str | None = None,
) -> str:
    """Segundo (y último) intento: vuelve a pedir el SQL incluyendo el SQL
    rechazado y el motivo exacto del rechazo."""
    sistema, usuario = prompt_sql_reintento_instacart(
        pregunta, descripcion_esquema, sql_anterior, motivo, contexto, retroalimentacion, lecciones,
    )
    crudo = client.chat_con_modelo(
        modelo or client.modelo_actual_instacart(), sistema, usuario, timeout_s=timeout_s,
        temperatura=temperatura,
    )
    return limpiar_sql(crudo)
