"""Fase 30b: num_ctx=8192 en TODAS las llamadas de generación a Ollama."""
from __future__ import annotations

import pytest

from src.llm import client


class _ClienteFalso:
    llamadas: list[dict] = []

    def __init__(self, timeout=None):
        pass

    def chat(self, **kwargs):
        _ClienteFalso.llamadas.append(kwargs)
        if kwargs.get("stream"):
            return iter([{"message": {"content": "hola"}, "done": True}])
        return {"message": {"content": "hola"}}


@pytest.fixture()
def falso(monkeypatch):
    _ClienteFalso.llamadas = []
    monkeypatch.setattr(client.ollama, "Client", _ClienteFalso)
    monkeypatch.setattr(client.ollama, "chat", lambda **kw: _ClienteFalso().chat(**kw))
    monkeypatch.delenv("VENTIA_NUM_CTX", raising=False)
    return _ClienteFalso.llamadas


def test_num_ctx_llega_a_las_tres_formas_de_llamar(falso):
    client.chat_con_modelo("m", "s", "u", timeout_s=5)           # con timeout (SQL, juez, revisor)
    client.chat_con_modelo("m", "s", "u")                        # sin timeout
    list(client.chat_con_modelo_stream("m", "s", "u", timeout_s=5))  # redacción en streaming
    assert [c["options"] for c in falso] == [{"temperature": 0.0, "num_ctx": 8192}] * 3


def test_override_por_entorno_y_cero_desactiva(falso, monkeypatch):
    monkeypatch.setenv("VENTIA_NUM_CTX", "16384")
    client.chat_con_modelo("m", "s", "u", temperatura=0.5)
    monkeypatch.setenv("VENTIA_NUM_CTX", "0")
    client.chat_con_modelo("m", "s", "u")
    assert falso[0]["options"] == {"temperature": 0.5, "num_ctx": 16384}
    assert falso[1]["options"] == {"temperature": 0.0}


def test_igual_para_base_y_few_shot():
    # La opción no depende de la técnica: sale solo de client (ningún otro módulo arma opciones).
    assert client.NUM_CTX_DEFAULT == 8192


def test_x8_ya_no_acierta_por_casualidad_con_solo_el_nombre():
    from src.llm import validacion_fuera_de_muestra as vfm
    x8 = next(p for p in vfm.cargar_preguntas() if p["id"] == "X8")
    ref = [{"product_name": "Banana", "veces_primero": 115521}, {"product_name": "Bag of Organic Bananas",
                                                                 "veces_primero": 82877}]
    assert not vfm.calificar(x8, ref, [("Banana",)])[0], "solo el nombre (el falso positivo de base)"
    assert not vfm.calificar(x8, ref, [("Banana", 491291)])[0], "el más vendido, no el primero en el carrito"
    assert vfm.calificar(x8, ref, [("Banana", 115521)])[0]
    x4 = next(p for p in vfm.cargar_preguntas() if p["id"] == "X4")
    assert vfm.calificar(x4, [{"product_name": "Banana", "veces_comprado": 491291}], [("Banana",)])[0], \
        "las demás no cambian"
