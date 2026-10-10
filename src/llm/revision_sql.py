"""Fase 27: revisor de la SENTENCIA SQL del chat libre.

En el chat libre no hay respuesta de referencia, así que el juez no puede
decidir si la respuesta final es correcta. Su papel se limita a la sentencia
SQL: ¿tiene sentido para la pregunta?, ¿usa columnas y JOINs que existen en
el esquema real?, ¿hay señales de un enfoque raro? Si la marca como
sospechosa, el chat reintenta una vez con ese motivo y la bitácora guarda la
lección (`src.llm.bitacora_chat`).

Se le pide prudencia ("ante la duda, no es sospechosa"): en las Fases 24-25,
los jueces de 8B/7B se equivocaban sobre todo rechazando cosas correctas, y
aquí un falso "sospechoso" cuesta un reintento que puede empeorar la consulta.
Por eso, si el reintento falla, el chat se queda con el resultado original.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from src.llm import client

TEMPERATURA_REVISOR = 0.0
FILAS_AL_REVISOR = 5

ORIGEN_REVISOR = "revisor"
ORIGEN_INVALIDO = "revisor_invalido"
ORIGEN_ERROR = "revisor_error"

_SISTEMA_REVISOR = """\
Eres un revisor de consultas SQL para DuckDB sobre el dataset Instacart. NO
conoces la respuesta correcta y NO debes juzgar si el resultado es correcto:
revisas solo la SENTENCIA SQL frente a la pregunta y al esquema real.

Revisa:
1. Si tiene sentido para la pregunta: agrupa, filtra, ordena y limita por lo
   que la pregunta pide (ni más ni menos).
2. Si cada columna está en la tabla que se usa y cada JOIN une por columnas
   que existen en AMBAS tablas, según el ESQUEMA REAL de abajo.
3. Señales de un enfoque raro: promediar o sumar un id; una agregación sobre
   una columna que no corresponde a lo que se pregunta; un filtro que la
   pregunta no pide; un LIMIT que corta lo que se pide.

Marca "sospechoso": true SOLO si puedes señalar un problema concreto. Ante la
duda, false.

Responde ÚNICAMENTE con un objeto JSON:
{"sospechoso": true | false,
 "motivo": "<el problema concreto en una frase; vacío si no hay>",
 "leccion": "<una frase general para no repetirlo en otras preguntas; vacío si no hay>"}
"""


@dataclass
class RevisionSQL:
    sospechoso: bool
    motivo: str
    leccion: str
    origen: str
    crudo: str | None = None


def prompt_revision(pregunta: str, sql: str, describe: str, resultado: str) -> tuple[str, str]:
    usuario = (
        f"ESQUEMA REAL:\n{describe.strip()}\n\n"
        f"PREGUNTA:\n{pregunta.strip()}\n\n"
        f"SQL A REVISAR:\n{' '.join(sql.split())}\n\n"
        f"PRIMERAS FILAS DEL RESULTADO (solo para ver su forma):\n{resultado.strip()}"
    )
    return _SISTEMA_REVISOR, usuario


def _es_verdadero(v: object) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "sí", "si", "yes"))


def parsear_revision(crudo: str) -> RevisionSQL:
    """Salida del revisor -> `RevisionSQL`. Algo ilegible cuenta como NO
    sospechoso (no se reintenta por una respuesta que no se entiende)."""
    datos = None
    try:
        datos = json.loads(crudo)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", crudo or "", re.DOTALL)
        if m:
            try:
                datos = json.loads(m.group(0))
            except json.JSONDecodeError:
                datos = None
    if not isinstance(datos, dict) or "sospechoso" not in datos:
        return RevisionSQL(False, "", "", ORIGEN_INVALIDO, crudo)
    motivo = str(datos.get("motivo") or "").strip()
    sospechoso = _es_verdadero(datos.get("sospechoso")) and bool(motivo)
    return RevisionSQL(sospechoso, motivo, str(datos.get("leccion") or "").strip(), ORIGEN_REVISOR, crudo)


def revisar_sql(
    pregunta: str, sql: str, describe: str, resultado: str, *, modelo: str, timeout_s: float | None,
) -> RevisionSQL:
    """Pide al `modelo` que revise la sentencia (temperatura 0.0, JSON). Si
    la llamada falla, no es sospechosa: la revisión nunca tumba una respuesta."""
    sistema, usuario = prompt_revision(pregunta, sql, describe, resultado)
    try:
        crudo = client.chat_con_modelo(modelo, sistema, usuario, temperatura=TEMPERATURA_REVISOR,
                                       timeout_s=timeout_s, formato_json=True)
    except client.LlmNoDisponible as exc:
        return RevisionSQL(False, "", "", ORIGEN_ERROR, str(exc))
    return parsear_revision(crudo)
