"""Llamada 1 de la ruta Instacart: pregunta en español -> sentencia SELECT.

Misma forma que `src/llm/nl2sql.py`: no ejecuta nada contra la base de
datos ni valida seguridad, solo produce el texto SQL. `limpiar_sql()` se
REUSA tal cual de `src.llm.nl2sql` (es limpieza de texto genérica --
fences de markdown, prefijo "sql", texto antes del SELECT -- no depende de
Northwind ni de Instacart) en vez de duplicarla.

`modelo` es opcional (default: `client.modelo_actual_instacart()`) para que
el benchmark pueda probar varios modelos por el mismo camino que el chat.
"""

from __future__ import annotations

from src.llm import client
from src.llm.nl2sql import limpiar_sql
from src.llm.prompts_instacart import prompt_sql_instacart, prompt_sql_reintento_instacart

__all__ = ["limpiar_sql", "generar_sql_instacart", "generar_sql_reintento_instacart"]


def generar_sql_instacart(
    pregunta: str,
    descripcion_esquema: str,
    *,
    modelo: str | None = None,
    timeout_s: float | None = None,
) -> str:
    """Pide al modelo (Instacart, `client.modelo_actual_instacart()`) el
    SQL para `pregunta`, dado el esquema ya introspeccionado, y lo
    devuelve limpio."""
    sistema, usuario = prompt_sql_instacart(pregunta, descripcion_esquema)
    crudo = client.chat_con_modelo(
        modelo or client.modelo_actual_instacart(), sistema, usuario, timeout_s=timeout_s,
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
) -> str:
    """Segundo (y último) intento: vuelve a pedir el SQL incluyendo el SQL
    rechazado y el motivo exacto del rechazo."""
    sistema, usuario = prompt_sql_reintento_instacart(pregunta, descripcion_esquema, sql_anterior, motivo)
    crudo = client.chat_con_modelo(
        modelo or client.modelo_actual_instacart(), sistema, usuario, timeout_s=timeout_s,
    )
    return limpiar_sql(crudo)
