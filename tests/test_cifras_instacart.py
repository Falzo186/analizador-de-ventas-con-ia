"""Validación de cifras de la redacción (Fase 6, paso 0.5). Los esperados
están escritos a mano. El caso real es el de docs/FASE5_REPORTE.md,
sección 8: la base devolvió 3421083 y qwen2.5-coder:1.5b escribió
"34,210,83".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.db.instacart import construir_duckdb
from src.llm import client
from src.llm import responder_instacart as ri
from src.llm.cifras_instacart import (
    cifras_invalidas,
    fmt_valor,
    lecturas_numericas,
    respuesta_plantilla,
    valores_permitidos,
)
from tests.helpers_analytics import FIXTURE_DIR

PREGUNTA_REAL = "¿Cuántos pedidos hay en total?"
PERMITIDOS_REAL = valores_permitidos(PREGUNTA_REAL, ["count_star()"], [(3421083,)])


# ---------------------------------------------------------------------------
# Detección (sin modelo)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto", [
    "Hay 3421083 pedidos en total.",
    "Hay 3,421,083 pedidos en total.",
    "Hay 3.421.083 pedidos en total.",
    "Hay 3 421 083 pedidos en total.",
])
def test_cifra_real_bien_escrita_se_acepta(texto):
    assert cifras_invalidas(texto, PERMITIDOS_REAL) == []


@pytest.mark.parametrize("texto,alterada", [
    ("Hay 34,210,83 pedidos en total.", "34,210,83"),   # el caso real de Fase 5
    ("Hay 342,108,3 pedidos en total.", "342,108,3"),
    ("Hay 3,421,084 pedidos en total.", "3,421,084"),
    ("Hay 342108 pedidos en total.", "342108"),
])
def test_cifra_alterada_se_detecta(texto, alterada):
    assert cifras_invalidas(texto, PERMITIDOS_REAL) == [alterada]


def test_quitar_todos_los_separadores_no_basta():
    # "34,210,83" sin comas es 3421083: una regla ingenua lo aceptaría.
    assert lecturas_numericas("34,210,83") == []


def test_porcentaje_y_decimales_redondeados_se_aceptan():
    permitidos = valores_permitidos("¿tasa de recompra?", ["tasa"], [(0.59337,)])
    assert cifras_invalidas("La tasa es 59.3%.", permitidos) == []
    assert cifras_invalidas("La tasa es 0.59.", permitidos) == []
    assert cifras_invalidas("La tasa es 61.2%.", permitidos) == ["61.2"]


def test_numeros_de_la_pregunta_y_de_celdas_de_texto_se_aceptan():
    permitidos = valores_permitidos(
        "¿A qué departamento pertenece el producto con product_id = 1?",
        ["product_name", "n"], [("Organic 2% Milk", 7)],
    )
    assert cifras_invalidas("El producto 1, Organic 2% Milk, aparece 7 veces.", permitidos) == []


def test_plantilla_inserta_el_valor_real_preformateado():
    assert fmt_valor(3421083) == "3,421,083"
    assert respuesta_plantilla(["count_star()"], [(3421083,)]) == "Resultado (count_star()): 3,421,083."


# ---------------------------------------------------------------------------
# Corrección (modelo simulado)
# ---------------------------------------------------------------------------

def _doble(respuestas: list[str], llamadas: list):
    pendientes = iter(respuestas)

    def _f(modelo, sistema, usuario, **k):
        llamadas.append((sistema, usuario))
        return next(pendientes)

    return _f


def test_caso_real_si_el_reintento_tambien_altera_la_cifra_se_usa_plantilla(monkeypatch):
    llamadas: list = []
    monkeypatch.setattr(client, "chat_con_modelo", _doble(["Hay 34,210,83 pedidos."], llamadas))
    res = ri.ResultadoInstacart(pregunta=PREGUNTA_REAL, columnas=["count_star()"], filas=[(3421083,)])

    ri.finalizar_redaccion(res, "Hay 34,210,83 pedidos en total.")

    assert res.cifras_rechazadas == ["34,210,83"]
    assert res.origen_respuesta == "plantilla"
    assert "3,421,083" in res.respuesta
    assert not re.search(r"34,210,83|342,108", res.respuesta)
    # El reintento recibió la cifra alterada y el valor exacto preformateado.
    assert "34,210,83" in llamadas[0][1] and "3,421,083" in llamadas[0][1]
    assert len(llamadas) == 1  # un solo reintento, nunca dos


def test_caso_real_si_el_reintento_la_escribe_bien_se_usa_el_reintento(monkeypatch):
    llamadas: list = []
    monkeypatch.setattr(client, "chat_con_modelo", _doble(["Hay 3,421,083 pedidos en total."], llamadas))
    res = ri.ResultadoInstacart(pregunta=PREGUNTA_REAL, columnas=["count_star()"], filas=[(3421083,)])

    ri.finalizar_redaccion(res, "Hay 34,210,83 pedidos en total.")

    assert res.origen_respuesta == "reintento"
    assert res.respuesta == "Hay 3,421,083 pedidos en total."


def test_redaccion_correcta_no_llama_otra_vez_al_modelo(monkeypatch):
    llamadas: list = []
    monkeypatch.setattr(client, "chat_con_modelo", _doble([], llamadas))
    res = ri.ResultadoInstacart(pregunta=PREGUNTA_REAL, columnas=["count_star()"], filas=[(3421083,)])

    ri.finalizar_redaccion(res, "Hay 3421083 pedidos en total.")

    assert res.origen_respuesta == "modelo"
    assert res.respuesta == "Hay 3421083 pedidos en total."
    assert llamadas == []


def test_si_el_reintento_falla_por_timeout_se_usa_plantilla(monkeypatch):
    def _lento(*a, **k):
        raise client.LlmTimeout("sin respuesta")

    monkeypatch.setattr(client, "chat_con_modelo", _lento)
    res = ri.ResultadoInstacart(pregunta=PREGUNTA_REAL, columnas=["count_star()"], filas=[(3421083,)])

    ri.finalizar_redaccion(res, "Hay 34,210,83 pedidos en total.")

    assert res.origen_respuesta == "plantilla"
    assert res.respuesta == "Resultado (count_star()): 3,421,083."
    assert res.ok


@pytest.fixture()
def db_path(tmp_path) -> Path:
    ruta = tmp_path / "needs.duckdb"
    construir_duckdb(FIXTURE_DIR, ruta)
    return ruta


def test_flujo_completo_numero_inventado_sintetico(db_path, monkeypatch):
    """La fixture tiene 21 pedidos. El modelo (simulado) inventa 57, y en
    el reintento estricto escribe el valor real."""
    llamadas: list = []
    monkeypatch.setattr(client, "chat_con_modelo", _doble([
        "SELECT COUNT(*) AS n FROM orders",   # SQL
        "Hay 57 pedidos en total.",           # redacción con número inventado
        "Hay 21 pedidos en total.",           # reintento estricto
    ], llamadas))

    res = ri.responder_pregunta_instacart(PREGUNTA_REAL, db_path=db_path)

    assert res.ok, res.error
    assert res.filas == [(21,)]
    assert res.cifras_rechazadas == ["57"]
    assert res.origen_respuesta == "reintento"
    assert res.respuesta == "Hay 21 pedidos en total."
