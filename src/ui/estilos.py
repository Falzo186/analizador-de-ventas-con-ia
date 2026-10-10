"""Fase 27 (UX): estilos comunes del chat y del dashboard.

Streamlit no permite dar fondo a un bloque sin CSS. Se inyecta una sola hoja
chica con tres reglas, para que de un vistazo se distinga qué es qué:

- Tarjetas (`st.container(border=True)`): borde sólido (el de Streamlit, sin
  cambios). Es el contenido siempre visible.
- Bloques colapsables (`st.expander`): borde PUNTEADO, fondo tenue y título más
  chico. Es el detalle opcional, que no debe leerse como un párrafo más.
- Espaciado vertical uniforme entre bloques del mismo nivel.

Nada aquí cambia qué se muestra, solo cómo se ve.
"""

from __future__ import annotations

import streamlit as st

CSS = """
<style>
/* Contenido colapsable: punteado + fondo tenue, distinto de las tarjetas */
div[data-testid="stExpander"] details {
    border-style: dashed !important;
    background: rgba(127, 127, 127, 0.07);
}
div[data-testid="stExpander"] summary p {
    font-size: 0.92rem;
    font-weight: 600;
    opacity: 0.9;
}
/* Espaciado uniforme entre bloques del mismo nivel (las tarjetas con borde
   ya traen su propio padding en Streamlit 1.54) */
div[data-testid="stVerticalBlock"] {
    gap: 0.9rem;
}
</style>
"""


def aplicar() -> None:
    """Inyecta la hoja de estilos (una vez por ejecución del script)."""
    st.markdown(CSS, unsafe_allow_html=True)
