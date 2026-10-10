"""Modelo por defecto de la ruta Instacart (Fase 19).

Motivo: `MODELO_POR_DEFECTO_INSTACART` se cambió de `qwen2.5-coder:1.5b`
a `llama3.1:8b` con datos del oráculo de referencia (Fases 11-18, ver
docs/CLASIFICACION_FALLOS.md): 83% vs 100% de respuestas correctas, por
un costo medido de +0.4s por pregunta y +4GB de RAM/VRAM. Esta prueba
deja constancia del valor esperado y de que la variable de entorno
`VENTIA_LLM_MODELO_INSTACART` sigue pudiendo volver al modelo chico (p.
ej. en una máquina sin GPU) sin tocar código.
"""

from __future__ import annotations

from src.llm.client import MODELO_POR_DEFECTO_INSTACART, modelo_actual_instacart


def test_modelo_por_defecto_es_llama_8b():
    assert MODELO_POR_DEFECTO_INSTACART == "llama3.1:8b"


def test_modelo_actual_usa_el_default_sin_variable_de_entorno(monkeypatch):
    monkeypatch.delenv("VENTIA_LLM_MODELO_INSTACART", raising=False)
    assert modelo_actual_instacart() == "llama3.1:8b"


def test_variable_de_entorno_permite_volver_al_modelo_chico(monkeypatch):
    """P. ej. para una demo en una máquina sin GPU o con poca RAM."""
    monkeypatch.setenv("VENTIA_LLM_MODELO_INSTACART", "qwen2.5-coder:1.5b")
    assert modelo_actual_instacart() == "qwen2.5-coder:1.5b"
