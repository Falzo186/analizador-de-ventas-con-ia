"""Capa analítica sin IA para el dataset Instacart.

Nada aquí importa `streamlit` ni ningún módulo de `src.ui`: el resultado de
cada necesidad (`src.analytics.contrato.ResultadoNecesidad`) es un objeto
serializable a JSON que consumirán, por igual, el futuro dashboard, el PDF
determinista y la futura comparación contra la IA. Ver docs/NECESIDADES.md.
"""
