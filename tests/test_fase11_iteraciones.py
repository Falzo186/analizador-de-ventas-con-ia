"""Fase 11: iteraciones configurables + juez por IA contra la respuesta
correcta (`src.llm.iteraciones_juez`). Todo contra la fixture chica y con un
modelo simulado (sin Ollama).

`ModeloIteraciones` distingue el tipo de llamada por el mensaje de sistema
(SQL, redacción o juez) y registra modelo, temperatura y `formato_json` de
cada una, para comprobar que las iteraciones van a 0.5, el juez a 0.0 y el
chat de producción sigue en 0.0.
"""

from __future__ import annotations

import itertools
import json

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from src.dashboard import servicio
from src.llm import client
from src.llm import formato_entrega as fe
from src.llm import iteraciones_juez as ij
from src.llm import responder_instacart as ri
from src.pdf import generador as pdf_generador
from src.ui import app_instacart
from tests.helpers_analytics import construir_fixture

SQL_OK = "SELECT COUNT(*) AS n FROM orders"
REDACCION_OK = "Hay 21 pedidos."


def _juez(veredicto: str, justificacion: str = "motivo", diagnostico: str | None = None) -> str:
    """Salida del juez. Sin `diagnostico`, el formato de la Fase 11; con él,
    el de la Fase 24 ("comparacion" + "diagnostico_enfoque")."""
    if diagnostico is None:
        return json.dumps({"veredicto": veredicto, "justificacion": justificacion}, ensure_ascii=False)
    return json.dumps({"comparacion": justificacion, "veredicto": veredicto, "diagnostico_enfoque": diagnostico},
                      ensure_ascii=False)


#: Fase 25: salida de la llamada de diagnóstico (sin veredicto).
DIAGNOSTICO_OK = json.dumps({"comparacion": "difiere", "diagnostico_enfoque": "Revisa la métrica."},
                            ensure_ascii=False)


class ModeloIteraciones:
    """Doble de `client.chat_con_modelo` / `chat_con_modelo_stream`. Cada
    tipo de llamada devuelve sus respuestas en orden y cicla al agotarse."""

    def __init__(self, sql=(SQL_OK,), redaccion=(REDACCION_OK,), juez=(_juez("correcta"),),
                 diagnostico=(DIAGNOSTICO_OK,)):
        self.respuestas = {"sql": itertools.cycle(sql), "redaccion": itertools.cycle(redaccion),
                           "juez": itertools.cycle(juez), "diagnostico": itertools.cycle(diagnostico)}
        self.llamadas: list[dict] = []

    @staticmethod
    def _tipo(sistema: str) -> str:
        if sistema.startswith("Eres un evaluador") and "YA DETERMINÓ" in sistema:
            return "diagnostico"  # Fase 25: el juez solo explica un intento que ya falló
        if sistema.startswith("Eres un evaluador"):
            return "juez"
        if sistema.startswith("Eres un traductor"):
            return "sql"
        return "redaccion"

    def chat_con_modelo(self, modelo, sistema, usuario, **k):
        tipo = self._tipo(sistema)
        self.llamadas.append({"tipo": tipo, "modelo": modelo, "usuario": usuario,
                              "temperatura": k.get("temperatura", 0.0),
                              "formato_json": k.get("formato_json", False),
                              "timeout_s": k.get("timeout_s")})
        respuesta = next(self.respuestas[tipo])
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    def chat_con_modelo_stream(self, modelo, sistema, usuario, **k):
        self.llamadas.append({"tipo": "stream", "modelo": modelo, "usuario": usuario,
                              "temperatura": k.get("temperatura", 0.0), "formato_json": False,
                              "timeout_s": k.get("timeout_s")})
        for palabra in next(self.respuestas["redaccion"]).split(" "):
            yield palabra + " "

    def de_tipo(self, tipo: str) -> list[dict]:
        return [ll for ll in self.llamadas if ll["tipo"] == tipo]


@pytest.fixture(scope="module")
def rutas(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fase11")
    src = construir_fixture(tmp)
    return src, tmp


@pytest.fixture()
def parchear(rutas, monkeypatch):
    src, tmp = rutas
    for modulo in (servicio, pdf_generador):
        monkeypatch.setattr(modulo, "DB_PATH_DEFAULT", src)
        monkeypatch.setattr(modulo, "OUT_DIR_DEFAULT", tmp / "referencia")
    monkeypatch.setattr(pdf_generador, "OUT_PDF_DEFAULT", tmp / "pdf" / "reporte.pdf")
    monkeypatch.setattr(ri, "DB_PATH_DEFAULT", src)
    monkeypatch.setattr(client, "modelos_disponibles", lambda: ["qwen2.5-coder:1.5b", "qwen2.5-coder:14b"])
    monkeypatch.setattr(client, "modelos_cargados", lambda: [])

    def _instalar(modelo: ModeloIteraciones) -> ModeloIteraciones:
        monkeypatch.setattr(client, "chat_con_modelo", modelo.chat_con_modelo)
        monkeypatch.setattr(client, "chat_con_modelo_stream", modelo.chat_con_modelo_stream)
        return modelo

    return _instalar


# ---------------------------------------------------------------------------
# Iteraciones: N corridas independientes por modelo, temperaturas
# ---------------------------------------------------------------------------

def test_corre_n_iteraciones_por_modelo_en_orden(parchear):
    modelo = parchear(ModeloIteraciones())
    corrida = ij.correr_iteraciones("N3", modelos=["chico", "grande"], n=3, modo=ij.MODO_INDEPENDIENTE)

    assert [(it.modelo, it.numero) for it in corrida.iteraciones] == [
        ("chico", 1), ("chico", 2), ("chico", 3), ("grande", 1), ("grande", 2), ("grande", 3),
    ]
    # Independientes: cada iteración pide su propio SQL y su propia redacción.
    assert [ll["modelo"] for ll in modelo.de_tipo("sql")] == ["chico"] * 3 + ["grande"] * 3
    assert len(modelo.de_tipo("redaccion")) == 6
    for it in corrida.iteraciones:
        assert it.ok and it.sql == SQL_OK and it.respuesta == REDACCION_OK
        assert it.veredicto == "correcta" and it.origen_veredicto == ij.ORIGEN_JUEZ


def test_pipeline_a_temperatura_05_y_juez_a_00_con_el_mismo_modelo(parchear):
    modelo = parchear(ModeloIteraciones())
    ij.correr_iteraciones("N3", modelos=["chico", "grande"], n=2)

    pipeline = modelo.de_tipo("sql") + modelo.de_tipo("redaccion")
    assert pipeline and {ll["temperatura"] for ll in pipeline} == {ij.TEMPERATURA_ITERACIONES} == {0.5}
    assert not any(ll["formato_json"] for ll in pipeline)
    jueces = modelo.de_tipo("juez")
    assert [ll["modelo"] for ll in jueces] == ["chico", "chico", "grande", "grande"]
    # Fase 25: en la fixture la referencia de N3 está vacía (nada llega a 50 compras), así que el
    # chequeo automático marca todo como incorrecto y cada intento pide también su diagnóstico.
    diagnosticos = modelo.de_tipo("diagnostico")
    assert [ll["modelo"] for ll in diagnosticos] == ["chico", "chico", "grande", "grande"]
    assert {ll["temperatura"] for ll in jueces + diagnosticos} == {ij.TEMPERATURA_JUEZ} == {0.0}
    assert all(ll["formato_json"] for ll in jueces + diagnosticos)
    # Orden dentro de cada iteración: SQL -> redacción -> juez (medición) -> diagnóstico.
    assert [ll["tipo"] for ll in modelo.llamadas[:4]] == ["sql", "redaccion", "juez", "diagnostico"]


def test_reintentos_del_pipeline_tambien_van_a_temperatura_05(parchear):
    # SQL inválido -> reintento de SQL; cifra alterada -> reintento estricto.
    modelo = parchear(ModeloIteraciones(
        sql=["SELECT * FROM tabla_que_no_existe", SQL_OK], redaccion=["Hay 99 pedidos.", REDACCION_OK],
    ))
    corrida = ij.correr_iteraciones("N3", modelos=["chico"], n=1)
    it = corrida.iteraciones[0]
    assert it.intentos_sql == 2 and it.origen_respuesta == "reintento"
    assert len(modelo.de_tipo("sql")) == 2 and len(modelo.de_tipo("redaccion")) == 2
    assert {ll["temperatura"] for ll in modelo.llamadas if ll["tipo"] not in ("juez", "diagnostico")} == {0.5}


def test_juez_recibe_pregunta_referencia_y_respuesta_generada(parchear):
    modelo = parchear(ModeloIteraciones())
    corrida = ij.correr_iteraciones("N3", modelos=["chico"], n=1)

    usuario = modelo.de_tipo("juez")[0]["usuario"]
    assert ij.PREGUNTAS["N3"] in usuario
    assert corrida.referencia in usuario
    assert REDACCION_OK in usuario
    assert usuario.index("REFERENCIA") < usuario.index("INTENTO DEL ASISTENTE")
    # Fase 24: el juez ve también el SQL y el resultado del intento.
    assert f"SQL del intento:\n{SQL_OK}" in usuario
    assert "Resultado de ese SQL:\nn\n21" in usuario


@pytest.mark.parametrize("nid", sorted(ij.PREGUNTAS))
def test_referencia_reusa_el_resultado_determinista(parchear, nid):
    r = servicio.cargar_necesidad(nid).resultado
    texto = ij.referencia_de(nid)
    assert texto == ij.texto_referencia(r)
    # Fase 24: SQL de referencia, resultado crudo, hallazgos tal cual y cada
    # valor clave escalar con su etiqueta (las listas ya están en el resultado).
    assert f"SQL de referencia: {' '.join(r.consultas[0].sql.split())}" in texto
    assert "Resultado de referencia:" in texto
    for h in r.hallazgos:
        assert h in texto
    for v in r.valores_clave:
        assert (f"- {v.etiqueta}: " in texto) != isinstance(v.valor, list)
    # La pregunta es la de la definición de la necesidad (r.pregunta).
    assert ij.PREGUNTAS[nid] == r.pregunta


def test_resumen_por_modelo_cuenta_cada_veredicto(parchear):
    parchear(ModeloIteraciones(juez=[
        _juez("correcta"), _juez("parcialmente correcta"), _juez("incorrecta"), _juez("correcta"),  # chico
        _juez("correcta"), _juez("correcta"), _juez("correcta"), "no es json",                        # grande
    ]))
    # Fase 26: sin corte, para contar los 4 veredictos de cada modelo.
    corrida = ij.correr_iteraciones("N3", modelos=["chico", "grande"], n=4, arbitro=ij.ARBITRO_JUEZ,
                                    corta_al_acertar=False)
    chico, grande = corrida.resumen()["chico"], corrida.resumen()["grande"]

    assert (chico.n, chico.correctas, chico.parciales, chico.incorrectas, chico.sin_veredicto) == (4, 2, 1, 1, 0)
    assert chico.pct_correctas == 50.0
    assert (grande.correctas, grande.sin_veredicto) == (3, 1)
    assert grande.pct_correctas == 75.0  # "sin veredicto" no cuenta como correcta


def test_pipeline_fallido_es_incorrecta_sin_llamar_al_juez(parchear):
    modelo = parchear(ModeloIteraciones(sql=["DELETE FROM orders"]))
    corrida = ij.correr_iteraciones("N3", modelos=["chico"], n=2)

    assert not modelo.de_tipo("juez")
    for it in corrida.iteraciones:
        assert not it.ok and it.respuesta is None
        assert it.veredicto == ij.INCORRECTA and it.origen_veredicto == ij.ORIGEN_AUTOMATICO
        assert it.justificacion.startswith("El pipeline no produjo respuesta: No puedo ejecutar esa consulta")
    assert corrida.resumen()["chico"].pct_correctas == 0.0


def test_juez_que_no_responde_queda_sin_veredicto(parchear):
    parchear(ModeloIteraciones(juez=[client.LlmTimeout("El modelo 'x' no respondió en 30s")]))
    it = ij.correr_iteraciones("N3", modelos=["chico"], n=1, arbitro=ij.ARBITRO_JUEZ).iteraciones[0]
    assert it.ok
    assert it.veredicto == ij.SIN_VEREDICTO and it.origen_veredicto == ij.ORIGEN_JUEZ_ERROR
    assert "no respondió" in it.justificacion


def test_modelos_repetidos_se_corren_una_vez(parchear):
    parchear(ModeloIteraciones())
    corrida = ij.correr_iteraciones("N3", modelos=["mismo", "mismo"], n=2)
    assert corrida.modelos == ["mismo"] and len(corrida.iteraciones) == 2


@pytest.mark.parametrize("n", [0, ij.ITERACIONES_MAX + 1])
def test_n_fuera_de_rango_se_rechaza(n):
    with pytest.raises(ValueError):
        ij.correr_iteraciones("N3", modelos=["chico"], n=n)


def test_necesidad_desconocida_se_rechaza():
    with pytest.raises(ValueError):
        ij.correr_iteraciones("N99", modelos=["chico"], n=1)


def test_corrida_serializa_a_json_con_todo_lo_mostrado(parchear):
    parchear(ModeloIteraciones(juez=[_juez("parcialmente correcta", "acierta el día pero no la hora")]))
    corrida = ij.correr_iteraciones("N3", modelos=["chico"], n=1, arbitro=ij.ARBITRO_JUEZ)
    doc = json.loads(ij.a_json(corrida))
    assert doc["temperatura"] == 0.5 and doc["temperatura_juez"] == 0.0
    assert doc["resumen"]["chico"]["pct_correctas"] == 0.0
    it = doc["iteraciones"][0]
    assert it["sql"] == SQL_OK and it["respuesta"] == REDACCION_OK
    assert it["veredicto"] == "parcialmente correcta" and it["justificacion"] == "acierta el día pero no la hora"


# ---------------------------------------------------------------------------
# Parseo del veredicto
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("crudo, esperado", [
    (_juez("correcta"), "correcta"),
    (_juez("Correcta"), "correcta"),
    (_juez("CORRECTA"), "correcta"),
    (_juez("incorrecta"), "incorrecta"),
    (_juez("Incorrecta."), "incorrecta"),
    (_juez("parcialmente correcta"), "parcialmente correcta"),
    (_juez("parcialmente_correcta"), "parcialmente correcta"),
    (_juez("Parcial"), "parcialmente correcta"),
    ('Aquí va: {"veredicto": "incorrecta", "justificacion": "x"} fin', "incorrecta"),
    ('{"veredicto": "correcto", "justificación": "con acento"}', "correcta"),
])
def test_parsear_veredicto_normaliza(crudo, esperado):
    v = ij.parsear_veredicto(crudo)
    assert v.veredicto == esperado and v.origen == ij.ORIGEN_JUEZ and v.crudo == crudo


@pytest.mark.parametrize("crudo", ["", "correcta", "{}", '{"veredicto": "tal vez"}', '{"veredicto": 1}', "[1, 2]"])
def test_parsear_veredicto_ilegible_no_adivina(crudo):
    v = ij.parsear_veredicto(crudo)
    assert v.veredicto == ij.SIN_VEREDICTO and v.origen == ij.ORIGEN_JUEZ_INVALIDO
    assert repr(crudo) in v.justificacion


def test_justificacion_se_conserva():
    v = ij.parsear_veredicto(_juez("incorrecta", "dice día 3 y la referencia dice día 1"))
    assert v.justificacion == "dice día 3 y la referencia dice día 1"


# ---------------------------------------------------------------------------
# Producción no cambia: chat y entrega siguen a temperatura 0.0
# ---------------------------------------------------------------------------

def test_pipeline_por_defecto_sigue_a_temperatura_0(parchear):
    modelo = parchear(ModeloIteraciones(redaccion=["Hay 99 pedidos.", REDACCION_OK]))
    res = ri.responder_pregunta_instacart("¿Cuántos pedidos hay?", db_path=ri.DB_PATH_DEFAULT)
    assert res.ok and res.origen_respuesta == "reintento"
    assert modelo.llamadas and {ll["temperatura"] for ll in modelo.llamadas} == {0.0}
    assert not any(ll["formato_json"] for ll in modelo.llamadas)


def test_entrega_fase10_sigue_a_temperatura_0(parchear):
    modelo = parchear(ModeloIteraciones())
    assert fe.generar_entrega_ia("N1", modelo="m").ok
    assert {ll["temperatura"] for ll in modelo.llamadas} == {0.0}


class _OllamaFalso:
    """Registra los kwargs con que `client.chat_con_modelo` llama a Ollama."""

    def __init__(self):
        self.kwargs: list[dict] = []

    def chat(self, **kwargs):
        self.kwargs.append(kwargs)
        return {"message": {"content": "{}"}}


@pytest.mark.parametrize("timeout_s", [None, 5.0])
def test_formato_json_solo_cuando_se_pide(monkeypatch, timeout_s):
    falso = _OllamaFalso()
    monkeypatch.setattr(client.ollama, "chat", falso.chat)
    monkeypatch.setattr(client.ollama, "Client", lambda timeout=None: falso)

    client.chat_con_modelo("m", "s", "u", timeout_s=timeout_s)
    client.chat_con_modelo("m", "s", "u", timeout_s=timeout_s, formato_json=True, temperatura=0.0)
    assert "format" not in falso.kwargs[0]
    assert falso.kwargs[1]["format"] == "json"
    # Fase 30b: todas las llamadas llevan además num_ctx (8192 por defecto).
    assert falso.kwargs[0]["options"] == {"temperature": 0.0, "num_ctx": client.NUM_CTX_DEFAULT}


def test_timeout_del_juez_es_el_de_respuesta_por_defecto(parchear):
    modelo = parchear(ModeloIteraciones())
    ij.correr_iteraciones("N3", modelos=["chico"], n=1, timeout_sql_llm_s=11.0, timeout_respuesta_llm_s=22.0)
    assert modelo.de_tipo("sql")[0]["timeout_s"] == 11.0
    assert modelo.de_tipo("juez")[0]["timeout_s"] == 22.0


def test_respuesta_de_timeout_de_httpx_en_el_juez_se_traduce(monkeypatch):
    class _Lento:
        def chat(self, **kwargs):
            raise httpx.ReadTimeout("lento")

    monkeypatch.setattr(client.ollama, "Client", lambda timeout=None: _Lento())
    v = ij.juzgar("p", "r", "a", modelo="m", timeout_s=1.0)
    assert v.veredicto == ij.SIN_VEREDICTO and v.origen == ij.ORIGEN_JUEZ_ERROR


# ---------------------------------------------------------------------------
# UI: configuración en ⚙️ y resultados en el dashboard
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(parchear):
    def _abrir(modelo: ModeloIteraciones) -> AppTest:
        parchear(modelo)
        at = AppTest.from_file("src/ui/app_instacart.py", default_timeout=90)
        at.run()
        assert not list(at.exception), list(at.exception)
        return at

    return _abrir


def test_config_tiene_cantidad_de_iteraciones_con_default_5(app):
    at = app(ModeloIteraciones())
    campo = at.number_input(key=app_instacart.CLAVE_ITERACIONES)
    assert campo.label == "Cantidad de iteraciones"
    assert campo.value == ij.ITERACIONES_DEFAULT == 5
    assert campo.min == ij.ITERACIONES_MIN and campo.max == ij.ITERACIONES_MAX
    assert at.selectbox(key=app_instacart.CLAVE_MODELO_PEQUENO).value == "qwen2.5-coder:1.5b"
    assert at.selectbox(key=app_instacart.CLAVE_MODELO_GRANDE).value == "qwen2.5-coder:14b"
    # El selector del chat (Fase 9) sigue ahí y no cambia.
    assert at.selectbox(key="modelo_sel").value == "qwen2.5-coder:1.5b"


def _metricas_iteraciones(at: AppTest) -> dict[str, str]:
    """Solo las métricas del resumen de iteraciones (el panel de la
    necesidad tiene sus propias métricas de valores clave)."""
    # Fase 26: en autocorrección (con corte) la métrica es «Convergió en el intento k» / «No convergió».
    return {m.label: m.value for m in at.metric if m.value.endswith("% correctas") or "onvergió" in m.value}


def _a_dashboard(at: AppTest, nid: str = "N3") -> AppTest:
    at.button(key="vista_dashboard").click().run()
    at.radio(key=app_instacart.CLAVE_NECESIDAD).set_value(nid).run()
    assert not list(at.exception), list(at.exception)
    return at


def test_boton_corre_n_iteraciones_por_modelo_y_muestra_cada_intento(app):
    modelo = ModeloIteraciones(juez=[_juez("correcta", "coincide"), _juez("incorrecta", "no coincide")])
    at = app(modelo)
    at.number_input(key=app_instacart.CLAVE_ITERACIONES).set_value(2).run()
    # Fase 25: en independientes el juez sigue decidiendo (en autocorrección decide el chequeo).
    at.selectbox(key=app_instacart.CLAVE_MODO_ITERACION).set_value(ij.MODO_INDEPENDIENTE).run()
    at = _a_dashboard(at)
    assert any(e.label.startswith("🧪 Iteraciones con juez IA") for e in at.expander)

    at.button(key="iterar_N3").click().run()
    assert not list(at.exception), list(at.exception)

    corrida = at.session_state[app_instacart.CLAVE_CORRIDAS]["N3"]
    assert corrida.modelos == ["qwen2.5-coder:1.5b", "qwen2.5-coder:14b"]
    assert len(corrida.iteraciones) == 4
    assert [ll["modelo"] for ll in modelo.de_tipo("juez")] == ["qwen2.5-coder:1.5b"] * 2 + ["qwen2.5-coder:14b"] * 2

    assert _metricas_iteraciones(at) == {"Pequeño: qwen2.5-coder:1.5b": "50% correctas", "Grande: qwen2.5-coder:14b": "50% correctas"}
    textos = "\n".join(m.value for m in at.markdown)
    assert textos.count("🤖 redactada por el modelo): Hay 21 pedidos.") == 4
    assert textos.count("✅ correcta — coincide") == 2 and textos.count("❌ incorrecta — no coincide") == 2
    assert sum(1 for c in at.code if c.value == SQL_OK) == 4
    # Fase 23: la referencia ya no es una pestaña, va arriba de los intentos.
    assert [t.label for t in at.tabs] == ["qwen2.5-coder:1.5b", "qwen2.5-coder:14b"]
    assert any(t.value == corrida.referencia for t in at.text)
    assert any(b.proto.label == "⬇️ Descargar N3_iteraciones_juez.json" for b in at.get("download_button"))


def test_error_del_pipeline_se_muestra_en_el_intento(app):
    at = _a_dashboard(app(ModeloIteraciones(sql=["DELETE FROM orders"])))
    at.number_input(key=app_instacart.CLAVE_ITERACIONES).set_value(1).run()
    at.button(key="iterar_N3").click().run()
    assert not list(at.exception)
    assert any("No puedo ejecutar esa consulta" in e.value for e in at.error)
    assert set(_metricas_iteraciones(at).values()) == {"❌ No convergió (1 de 1 intentos)"}


def test_resultados_se_guardan_por_necesidad(app):
    at = _a_dashboard(app(ModeloIteraciones()))
    at.number_input(key=app_instacart.CLAVE_ITERACIONES).set_value(1).run()
    at.button(key="iterar_N3").click().run()
    at.radio(key=app_instacart.CLAVE_NECESIDAD).set_value("N1").run()
    assert not _metricas_iteraciones(at), "N1 todavía no tiene corrida"
    at.radio(key=app_instacart.CLAVE_NECESIDAD).set_value("N3").run()
    assert len(_metricas_iteraciones(at)) == 2


def test_el_chat_normal_sigue_a_temperatura_0(app):
    modelo = ModeloIteraciones(redaccion=["Hay 21 pedidos en total."])
    at = app(modelo)
    at.number_input(key=app_instacart.CLAVE_ITERACIONES).set_value(3).run()
    at.chat_input(key="chat_input").set_value("¿Cuántos pedidos hay?").run()
    assert at.session_state["historial"][-1]["contenido"] == "Hay 21 pedidos en total."
    assert modelo.llamadas and {ll["temperatura"] for ll in modelo.llamadas} == {0.0}
    assert not modelo.de_tipo("juez")
