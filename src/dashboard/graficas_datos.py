"""Selección de datos para la gráfica de cada necesidad (Fase 4; Fase 24).

Recibe un `ResultadoNecesidad` ya cargado (vía `src.dashboard.servicio`,
nunca calculado en vivo aquí) y devuelve un `pandas.DataFrame` listo para
graficar, usando SOLO la tabla que ya trae el JSON: ninguna función calcula
ni inventa datos nuevos.

Fase 24: las 10 necesidades son de una sola consulta, así que hay UNA
selección genérica, guiada por la definición de la necesidad
(`src.analytics.necesidades.DefNecesidad`): barras de `columna_nombre` vs.
`columna_valor` para las respuestas tipo lista/único (rankings). Las de tipo
escalar no tienen gráfica: su respuesta es un solo número (valor clave).

Este módulo NO importa `altair`, `matplotlib` ni `streamlit`: es la capa de
datos compartida entre `src/ui/dashboard.py` (Altair) y `src/pdf/generador.py`
(matplotlib), para que ambos dibujen exactamente lo mismo.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.analytics import necesidades as nec
from src.analytics.contrato import ResultadoNecesidad


@dataclass
class DatosGrafica:
    df: pd.DataFrame
    #: Columna categórica (eje de las barras) y columna numérica (largo).
    categoria: str
    valor: str
    titulo: str
    #: Fila(s) que responden la pregunta: las `filas_esperadas` primeras (todas
    #: si la pregunta no fija cuántas).
    filas_respuesta: int


def datos_grafica(r: ResultadoNecesidad) -> DatosGrafica | None:
    """Barras del ranking de la necesidad, o None si es de tipo escalar."""
    d = nec.DEFINICIONES[r.id]
    if d.tipo == nec.ESCALAR or d.columna_nombre is None:
        return None
    df = pd.DataFrame(r.tablas[nec.CONSULTA])
    nombres = [d.columna_nombre, *d.columnas_nombre_extra]
    # Fase 28: el par de N9 se grafica como "A + B"; un identificador numérico
    # (user_id, la hora) se grafica como categoría, no como cantidad.
    categoria = "par" if len(nombres) > 1 else d.columna_nombre
    if not df.empty:
        df[categoria] = df[nombres].astype(str).agg(" + ".join, axis=1)
        df = df[[categoria, d.columna_valor]].copy()
    return DatosGrafica(
        df=df, categoria=categoria, valor=d.columna_valor,
        titulo=f"{d.columna_valor} por {categoria} (las {len(df)} filas de la consulta de referencia)",
        filas_respuesta=d.filas_esperadas if d.filas_esperadas is not None else len(df),
    )
