"""Llamada 1: pregunta en español  ->  sentencia SELECT (texto).

Este módulo NO ejecuta nada contra la base de datos ni valida seguridad;
solo produce una cadena SQL a partir de la pregunta. Ejecutar y validar es
trabajo de responder.py / safety.py.
"""

from __future__ import annotations

import re

from src.llm import client
from src.llm.prompts import prompt_sql

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


def generar_sql(pregunta: str) -> str:
    """Pide al modelo el SQL para `pregunta` y lo devuelve ya limpio."""
    sistema, usuario = prompt_sql(pregunta)
    crudo = client.chat(sistema, usuario)
    return limpiar_sql(crudo)
