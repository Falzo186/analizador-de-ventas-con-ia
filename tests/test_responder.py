"""Prueba del orquestador SIN Ollama real: se reemplaza `client.chat` por un
doble que devuelve un texto fijo.
"""

from __future__ import annotations

from src.generator.seed import sembrar_productos
from src.llm import client, responder


def test_sql_de_escritura_del_modelo_no_tumba_el_programa(monkeypatch):
    # El "modelo" propone un DELETE: debe reportarse como error, no explotar.
    monkeypatch.setattr(client, "chat", lambda *a, **k: "DELETE FROM ventas")

    res = responder.responder_pregunta("borra todo")

    assert res.ok is False
    assert "seguridad" in res.error.lower()
    assert res.filas is None


def test_flujo_feliz_con_modelo_simulado(monkeypatch):
    sembrar_productos()

    respuestas = iter([
        "SELECT COUNT(*) FROM productos",          # llamada 1: SQL
        "Hay 54 productos en el catálogo.",        # llamada 2: redacción
    ])
    monkeypatch.setattr(client, "chat", lambda *a, **k: next(respuestas))

    res = responder.responder_pregunta("¿cuántos productos hay?")

    assert res.ok
    assert res.sql == "SELECT COUNT(*) FROM productos"
    assert res.filas[0][0] > 0
    assert res.respuesta == "Hay 54 productos en el catálogo."
