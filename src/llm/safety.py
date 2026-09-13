"""Validación de seguridad del SQL que devuelve el modelo.

Nunca ejecutamos SQL del modelo sin pasarlo por `validar_sql()` primero.
Reglas:

* Debe ser UNA sola sentencia.
* Debe empezar con SELECT.
* No puede contener palabras clave de escritura o de esquema.

Si algo falla, se lanza `SqlNoSeguro`. Quien orquesta (responder.py) la
atrapa y la reporta como un error normal: el programa NO se cae.
"""

from __future__ import annotations

import re

#: Palabras que jamás deben aparecer en una consulta de solo lectura.
PALABRAS_PROHIBIDAS: tuple[str, ...] = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "REPLACE", "TRUNCATE", "MERGE", "UPSERT",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "GRANT",
)


class SqlNoSeguro(Exception):
    """El SQL propuesto por el modelo no pasó la validación."""


def _sin_comentarios(sql: str) -> str:
    """Quita comentarios -- ... y /* ... */ para que no escondan nada."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql


def validar_sql(sql: str) -> str:
    """Devuelve el SQL normalizado si es seguro; si no, lanza `SqlNoSeguro`."""
    if not sql or not sql.strip():
        raise SqlNoSeguro("La consulta está vacía.")

    limpio = _sin_comentarios(sql).strip()

    # Quitar como máximo un punto y coma final (una sentencia terminada).
    if limpio.endswith(";"):
        limpio = limpio[:-1].strip()

    # Cualquier punto y coma restante => hay más de una sentencia.
    if ";" in limpio:
        raise SqlNoSeguro("La consulta contiene más de una sentencia.")

    if not re.match(r"(?is)^select\b", limpio):
        raise SqlNoSeguro("La consulta debe empezar con SELECT.")

    for palabra in PALABRAS_PROHIBIDAS:
        if re.search(rf"(?i)\b{palabra}\b", limpio):
            raise SqlNoSeguro(f"Palabra prohibida en la consulta: {palabra}.")

    return limpio


def es_seguro(sql: str) -> bool:
    """Versión booleana de `validar_sql`, cómoda para condicionales/tests."""
    try:
        validar_sql(sql)
        return True
    except SqlNoSeguro:
        return False
