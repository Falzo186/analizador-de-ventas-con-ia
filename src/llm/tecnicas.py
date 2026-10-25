"""Fase 30: técnicas de contexto para la llamada de SQL del modelo local.

Son las columnas de la tabla de comparación del profesor:

| Técnica  | Estado     | Qué agrega al prompt                                                |
|----------|------------|---------------------------------------------------------------------|
| base     | activa     | Nada: esquema + reglas de negocio (+ bitácora en el chat), como hasta la Fase 29 |
| few_shot | activa     | Ejemplos resueltos de necesidades negativas parecidas (`src.llm.few_shot`) |
| rag      | pendiente  | Recuperar contexto con embeddings (para después, ver docs/FEW_SHOT.md) |
| harness  | pendiente  | Bucle de agente con herramientas (para después, ver docs/FEW_SHOT.md) |

La técnica se elige por parámetro (`responder_pregunta_instacart(tecnica=...)`,
`--tecnica` en el CLI de iteraciones, selector del dashboard) o por la variable
de entorno VENTIA_TECNICA. Elegir una pendiente lanza `TecnicaPendiente`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from src.llm import few_shot

BASE = "base"
FEW_SHOT = "few_shot"
RAG = "rag"
HARNESS = "harness"

ACTIVA = "activa"
PENDIENTE = "pendiente"
ETIQUETA_PENDIENTE = "pendiente (siguiente fase)"

#: Interruptor: la técnica por defecto sigue siendo «base» hasta que la matriz
#: de técnicas (docs/resultados/matriz_tecnicas.json) demuestre mejora. Para
#: cambiarla sin tocar el código: VENTIA_TECNICA=few_shot.
TECNICA_DEFAULT = BASE


@dataclass(frozen=True)
class Tecnica:
    id: str
    etiqueta: str
    estado: str
    descripcion: str


TECNICAS: dict[str, Tecnica] = {
    BASE: Tecnica(BASE, "Base", ACTIVA, "Esquema y reglas de negocio, sin ejemplos."),
    FEW_SHOT: Tecnica(FEW_SHOT, "Few shot", ACTIVA,
                      "Además, los ejemplos resueltos (necesidades negativas) más parecidos a la pregunta."),
    RAG: Tecnica(RAG, "RAG", PENDIENTE,
                 "Recuperar contexto con embeddings sobre la documentación del esquema, reglas y glosario."),
    HARNESS: Tecnica(HARNESS, "Harness", PENDIENTE,
                     "Bucle de agente con herramientas (DESCRIBE, SQL de prueba, leer errores)."),
}
TECNICAS_ACTIVAS = tuple(t for t, d in TECNICAS.items() if d.estado == ACTIVA)


class TecnicaPendiente(ValueError):
    """Se pidió una técnica registrada pero todavía no implementada."""


def validar_tecnica(tecnica: str) -> str:
    if tecnica not in TECNICAS:
        raise ValueError(f"Técnica desconocida: {tecnica!r}. Opciones: {', '.join(TECNICAS)}")
    if TECNICAS[tecnica].estado == PENDIENTE:
        raise TecnicaPendiente(
            f"La técnica «{TECNICAS[tecnica].etiqueta}» está registrada pero {ETIQUETA_PENDIENTE}: "
            f"todavía no está implementada. Usa una de: {', '.join(TECNICAS_ACTIVAS)}."
        )
    return tecnica


def tecnica_por_defecto() -> str:
    """VENTIA_TECNICA si está definida (y es válida); si no, `TECNICA_DEFAULT`."""
    return validar_tecnica(os.environ.get("VENTIA_TECNICA", "").strip() or TECNICA_DEFAULT)


def contexto_de_tecnica(tecnica: str, pregunta: str) -> few_shot.ContextoFewShot:
    """Lo que la técnica agrega al prompt de SQL de `pregunta` (vacío en base)."""
    if validar_tecnica(tecnica) == FEW_SHOT:
        return few_shot.contexto_few_shot(pregunta)
    return few_shot.ContextoFewShot()
