"""App de dos paneles (Fase 6), sin Ollama: el modelo se reemplaza por
dobles de `client.chat_con_modelo` (SQL) y `client.chat_con_modelo_stream`
(redacción), y el dashboard y el chat apuntan a la fixture chica.

También hay tests de streaming de verdad (sin AppTest): un servidor HTTP
local que imita `/api/chat` de Ollama manda fragmentos NDJSON espaciados
en el tiempo, y se comprueba que el primero llega ANTES de que el servidor
termine (no todo de una vez).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from streamlit.testing.v1 import AppTest

from src.dashboard import servicio
from src.llm import client
from src.llm import responder_instacart as ri
from src.pdf import generador as pdf_generador
from src.ui import app_instacart
from tests.helpers_analytics import construir_fixture

RUTA_APP = "src/ui/app_instacart.py"


class ModeloSimulado:
    """Doble del modelo. `sql` son las respuestas de la llamada de SQL en
    orden; `redacciones` las de la redacción en stream (cada una se entrega
    en fragmentos de 1 palabra). Registra los mensajes de usuario."""

    def __init__(self, sql: list[str], redacciones: list[str]):
        self.sql = list(sql)
        self.redacciones = list(redacciones)
        self.prompts_sql: list[str] = []

    def chat_con_modelo(self, modelo, sistema, usuario, **k):
        self.prompts_sql.append(usuario)
        return self.sql.pop(0)

    def chat_con_modelo_stream(self, modelo, sistema, usuario, **k):
        for palabra in self.redacciones.pop(0).split(" "):
            yield palabra + " "


@pytest.fixture(scope="module")
def rutas(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("app_instacart")
    src, agg = construir_fixture(tmp)
    return src, agg, tmp


@pytest.fixture()
def app(rutas, monkeypatch):
    src, agg, tmp = rutas
    for modulo in (servicio, pdf_generador):
        monkeypatch.setattr(modulo, "DB_PATH_DEFAULT", src)
        monkeypatch.setattr(modulo, "AGG_PATH_DEFAULT", agg)
        monkeypatch.setattr(modulo, "OUT_DIR_DEFAULT", tmp / "referencia")
    monkeypatch.setattr(pdf_generador, "OUT_PDF_DEFAULT", tmp / "pdf" / "reporte.pdf")
    monkeypatch.setattr(ri, "DB_PATH_DEFAULT", src)

    def _instalar(modelo: ModeloSimulado) -> AppTest:
        monkeypatch.setattr(client, "chat_con_modelo", modelo.chat_con_modelo)
        monkeypatch.setattr(client, "chat_con_modelo_stream", modelo.chat_con_modelo_stream)
        at = AppTest.from_file(RUTA_APP, default_timeout=90)
        at.run()
        assert not list(at.exception), f"la app falla al cargar: {list(at.exception)}"
        return at

    return _instalar


def _preguntar(at: AppTest, pregunta: str) -> AppTest:
    at.chat_input(key="chat_input").set_value(pregunta).run()
    assert not list(at.exception), f"excepción sin capturar: {list(at.exception)}"
    return at


def _textos(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def test_layout_vista_alternante_arranca_en_chat_a_todo_el_ancho(app):
    # Fase 8: reemplaza a test_layout_de_dos_columnas_chat_izquierda_dashboard_derecha
    # (Fase 6). Ya no hay dos columnas: arranca en la vista de chat y el
    # dashboard NO está en pantalla hasta elegirlo.
    at = app(ModeloSimulado([], []))
    assert at.session_state["vista"] == "chat"
    assert any("Pregúntale a la IA" in s.value for s in at.subheader)
    assert not at.radio, "el selector del dashboard no debe estar en la vista de chat"
    assert not any(b.label == "📄 Generar/Actualizar PDF" for b in at.button)
    assert not at.sidebar.radio, "el dashboard no debe quedar en la barra lateral"


def test_no_importa_la_ruta_northwind():
    fuente = open(RUTA_APP, encoding="utf-8").read()
    assert "src.llm.responder import" not in fuente
    assert "from src.llm.responder " not in fuente
    assert "src.ui.app " not in fuente and "import app\n" not in fuente


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def test_pregunta_muestra_sql_y_respuesta(app):
    at = _preguntar(app(ModeloSimulado(
        ["SELECT COUNT(*) AS n FROM orders"], ["Hay 21 pedidos en total."],
    )), "¿Cuántos pedidos hay?")

    assert "Hay 21 pedidos en total." in _textos(at)
    assert [c.value for c in at.code] == ["SELECT COUNT(*) AS n FROM orders"]
    assert any(e.label.startswith("Detalles") for e in at.expander)
    ultimo = at.session_state["historial"][-1]
    assert ultimo["rol"] == "asistente" and ultimo["sql"] == "SELECT COUNT(*) AS n FROM orders"
    assert ultimo["metadatos"]["filas"] == [[21]]


def test_pregunta_de_escritura_se_muestra_como_rechazo_claro(app):
    at = _preguntar(app(ModeloSimulado(["DELETE FROM orders"], [])), "Borra todos los pedidos.")

    errores = [e.value for e in at.error]
    assert len(errores) == 1
    assert "Consulta rechazada por las guardas de seguridad" in errores[0]
    assert "Traceback" not in errores[0]
    assert [c.value for c in at.code] == ["DELETE FROM orders"]
    assert at.session_state["historial"][-1]["metadatos"]["tipo_error"] == "rechazo"


def test_timeout_del_modelo_se_muestra_como_error_controlado(app):
    modelo = ModeloSimulado([], [])

    def _lento(*a, **k):
        raise client.LlmTimeout("El modelo 'x' no respondió en 30s y se canceló la espera.")

    modelo.chat_con_modelo = _lento
    at = _preguntar(app(modelo), "¿Cuántos pedidos hay?")

    errores = [e.value for e in at.error]
    assert len(errores) == 1 and "Se canceló por tiempo" in errores[0]
    assert at.session_state["historial"][-1]["metadatos"]["tipo_error"] == "timeout"


def test_cifra_alterada_en_el_stream_se_avisa_y_se_corrige(app):
    at = _preguntar(app(ModeloSimulado(
        ["SELECT COUNT(*) AS n FROM orders", "Hay 21 pedidos."],   # SQL + reintento estricto
        ["Hay 2,1 pedidos en total."],                            # stream con cifra alterada
    )), "¿Cuántos pedidos hay?")

    assert any("alteró la cifra 2,1" in w.value for w in at.warning)
    assert "**Respuesta corregida:** Hay 21 pedidos." in _textos(at)
    assert at.session_state["historial"][-1]["contenido"] == "Hay 21 pedidos."


def test_historial_persiste_entre_dos_preguntas_y_da_contexto(app):
    modelo = ModeloSimulado(
        ["SELECT COUNT(*) AS n FROM orders WHERE eval_set = 'train'",
         "SELECT COUNT(*) AS n FROM orders WHERE eval_set = 'test'"],
        ["Ese es el total de tipo train.", "Ese es el total de tipo test."],
    )
    at = _preguntar(app(modelo), "¿Cuántos pedidos hay de tipo train?")
    at = _preguntar(at, "¿y de tipo test?")

    historial = at.session_state["historial"]
    assert [m["rol"] for m in historial] == ["usuario", "asistente", "usuario", "asistente"]
    assert len(at.chat_message) == 4, "los dos turnos deben seguir visibles tras el segundo rerun"
    # La segunda llamada de SQL recibió el turno anterior como contexto.
    assert "Pregunta: ¿Cuántos pedidos hay de tipo train?" in modelo.prompts_sql[1]
    assert "eval_set = 'train'" in modelo.prompts_sql[1]
    assert "Pregunta actual (genera el SQL SOLO para esta): ¿y de tipo test?" in modelo.prompts_sql[1]
    assert historial[-1]["metadatos"]["contexto_usado"] == 1


def test_dashboard_sigue_funcionando_dentro_de_la_app(app):
    at = _preguntar(app(ModeloSimulado(
        ["SELECT COUNT(*) AS n FROM orders"], ["Hay 21 pedidos en total."],
    )), "¿Cuántos pedidos hay?")

    # Fase 8: antes usaba at.columns[1] (panel derecho de la Fase 6); ahora
    # se entra a la vista de dashboard con el ícono 📊.
    at.button(key="vista_dashboard").click().run()
    for nid in ("N3", "N9", "N11"):
        at.radio(key="necesidad_sel").set_value(nid).run()
        assert not list(at.exception), f"{nid}: {list(at.exception)}"
        assert any(t.value.startswith(f"{nid} —") for t in at.title)
        assert at.metric
    # Usar el dashboard no borra la conversación.
    assert len(at.session_state["historial"]) == 2


def test_contexto_solo_turnos_exitosos_y_como_maximo_n():
    def turno(pregunta, sql, filas):
        return {"rol": "asistente", "contenido": "", "sql": sql,
                "metadatos": {"pregunta": pregunta, "filas": filas}}

    historial = [turno(f"p{i}", f"SELECT {i}", [[i]]) for i in range(5)]
    historial.append(turno("borra", "DELETE FROM orders", None))
    assert app_instacart.contexto_de_seguimiento(historial) == [
        ("p2", "SELECT 2"), ("p3", "SELECT 3"), ("p4", "SELECT 4"),
    ]


# ---------------------------------------------------------------------------
# Streaming real: llega en fragmentos, no todo de una vez
# ---------------------------------------------------------------------------

@pytest.fixture()
def ollama_que_transmite(monkeypatch):
    """Imita `/api/chat` con `stream: true`: manda 4 líneas NDJSON con
    0.4s entre una y otra."""
    fragmentos = ["Hay ", "21 ", "pedidos ", "en total."]
    PAUSA_S = 0.4

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            cuerpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            assert cuerpo.get("stream") is True
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i, texto in enumerate(fragmentos + [""]):
                linea = json.dumps({"model": "falso", "done": i == len(fragmentos),
                                    "message": {"role": "assistant", "content": texto}}).encode() + b"\n"
                self.wfile.write(f"{len(linea):x}\r\n".encode() + linea + b"\r\n")
                self.wfile.flush()
                time.sleep(PAUSA_S)
            self.wfile.write(b"0\r\n\r\n")

        def log_message(self, *a):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    servidor.daemon_threads = True
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{servidor.server_address[1]}")
    yield fragmentos, PAUSA_S
    servidor.shutdown()
    servidor.server_close()


def test_stream_llega_en_fragmentos_no_todo_de_una_vez(ollama_que_transmite):
    fragmentos, pausa_s = ollama_que_transmite
    t0 = time.perf_counter()
    llegadas = [(texto, time.perf_counter() - t0)
                for texto in client.chat_con_modelo_stream("falso", "s", "u", timeout_s=5)]

    assert [t for t, _ in llegadas] == fragmentos
    total = len(fragmentos) * pausa_s
    assert llegadas[0][1] < pausa_s, f"el primer fragmento tardó {llegadas[0][1]:.2f}s: no hubo streaming"
    assert llegadas[-1][1] >= total - pausa_s - 0.05
    assert llegadas[0][1] < llegadas[-1][1] - 2 * pausa_s


def test_redaccion_en_stream_entrega_fragmentos_y_luego_valida(ollama_que_transmite, monkeypatch):
    fragmentos, _ = ollama_que_transmite
    res = ri.ResultadoInstacart(pregunta="¿Cuántos pedidos hay?", sql="SELECT 1",
                                columnas=["n"], filas=[(21,)])
    generador = ri.redaccion_en_stream(res, modelo="falso", timeout_s=5)

    assert next(generador) == fragmentos[0]
    assert res.respuesta is None, "no debe finalizar antes de agotar el stream"
    resto = list(generador)
    assert "".join([fragmentos[0], *resto]) == "Hay 21 pedidos en total."
    assert res.respuesta == "Hay 21 pedidos en total." and res.origen_respuesta == "modelo"


# ---------------------------------------------------------------------------
# Fase 8: vista alternante estilo Claude.ai
# ---------------------------------------------------------------------------

def _en_chat(at: AppTest) -> bool:
    return any("Pregúntale a la IA" in s.value for s in at.subheader)


def _en_dashboard(at: AppTest) -> bool:
    return bool(at.radio) and any(b.label == "📄 Generar/Actualizar PDF" for b in at.button)


def test_iconos_existen_y_el_activo_se_distingue(app):
    at = app(ModeloSimulado([], []))
    chat, dash = at.button(key="vista_chat"), at.button(key="vista_dashboard")
    assert (chat.label, dash.label) == ("💬", "📊")
    assert (chat.proto.type, dash.proto.type) == ("primary", "secondary")

    dash.click().run()
    assert (at.button(key="vista_chat").proto.type, at.button(key="vista_dashboard").proto.type) == (
        "secondary", "primary")


def test_iconos_alternan_la_vista_a_todo_el_ancho(app):
    at = app(ModeloSimulado([], []))
    assert _en_chat(at) and not _en_dashboard(at)

    at.button(key="vista_dashboard").click().run()
    assert not list(at.exception)
    assert _en_dashboard(at) and not _en_chat(at)
    assert not at.chat_input, "el chat no debe estar en pantalla en la vista de dashboard"

    at.button(key="vista_chat").click().run()
    assert _en_chat(at) and not _en_dashboard(at)
    assert at.chat_input


def test_fijado_de_la_sidebar_abre_el_dashboard_con_esa_necesidad(app):
    at = app(ModeloSimulado([], []))
    assert len(at.sidebar.button) == 11
    assert at.sidebar.button(key="fijado_N3").label == "N3 · Picos de demanda"

    at.sidebar.button(key="fijado_N3").click().run()

    assert not list(at.exception)
    assert at.session_state["vista"] == "dashboard"
    assert at.radio(key="necesidad_sel").value == "N3"
    assert any(t.value == "N3 — Picos de demanda" for t in at.title)
    assert at.sidebar.button(key="fijado_N3").proto.type == "primary"
    assert at.sidebar.button(key="fijado_N1").proto.type == "secondary"


def test_la_necesidad_elegida_sobrevive_a_pasar_por_el_chat(app):
    at = app(ModeloSimulado([], []))
    at.sidebar.button(key="fijado_N7").click().run()
    at.button(key="vista_chat").click().run()
    at.button(key="vista_dashboard").click().run()
    assert any(t.value.startswith("N7 —") for t in at.title)


def test_historial_persiste_al_ir_al_dashboard_y_volver(app):
    at = _preguntar(app(ModeloSimulado(
        ["SELECT COUNT(*) AS n FROM orders"], ["Hay 21 pedidos en total."],
    )), "¿Cuántos pedidos hay?")
    antes = (len(at.chat_message), len(at.session_state["historial"]))

    at.button(key="vista_dashboard").click().run()
    assert not at.chat_message
    at.button(key="vista_chat").click().run()

    assert (len(at.chat_message), len(at.session_state["historial"])) == antes == (2, 2)
    assert "Hay 21 pedidos en total." in _textos(at)


def test_aviso_de_respuesta_en_curso_no_queda_en_pantalla_al_terminar(app):
    at = _preguntar(app(ModeloSimulado(
        ["SELECT COUNT(*) AS n FROM orders"], ["Hay 21 pedidos en total."],
    )), "¿Cuántos pedidos hay?")
    assert at.session_state["pregunta_en_curso"] is None
    assert not any("Respondiendo" in c.value for c in at.caption)


def test_sidebar_no_tiene_chats_recientes(app):
    at = _preguntar(app(ModeloSimulado(
        ["SELECT COUNT(*) AS n FROM orders"], ["Hay 21 pedidos en total."],
    )), "¿Cuántos pedidos hay?")
    textos = [getattr(el, "value", "") or getattr(el, "label", "") for el in at.sidebar]
    etiquetas = [b.label for b in at.sidebar.button]
    assert etiquetas == [f"N{i} · {app_instacart.TITULOS_NECESIDADES[f'N{i}']}" for i in range(1, 12)]
    for texto in textos:
        assert not any(p in str(texto).lower() for p in ("reciente", "historial", "conversaci", "¿cuántos pedidos"))


def test_pregunta_pendiente_se_avisa_en_dashboard_y_se_procesa_al_volver(app):
    """Simula el estado que deja un cambio de vista a mitad de respuesta
    (medido con el servidor real en docs/FASE8_REPORTE.md): la pregunta
    queda en `pregunta_en_curso` y la vista pasa a dashboard."""
    modelo = ModeloSimulado(["SELECT COUNT(*) AS n FROM orders"], ["Hay 21 pedidos en total."])
    at = app(modelo)
    at.session_state["pregunta_en_curso"] = "¿Cuántos pedidos hay?"
    at.session_state["vista"] = "dashboard"
    at.run()

    assert any("pregunta pendiente" in i.value and "¿Cuántos pedidos hay?" in i.value for i in at.info)
    assert modelo.prompts_sql == [], "en la vista de dashboard no se procesa la pregunta"

    at.button(key="vista_chat").click().run()

    assert len(modelo.prompts_sql) == 1
    assert at.session_state["pregunta_en_curso"] is None
    assert [m["rol"] for m in at.session_state["historial"]] == ["usuario", "asistente"]
    assert "Hay 21 pedidos en total." in _textos(at)


# ---------------------------------------------------------------------------
# HOTFIX 1: un cambio de vista no repite ni descarta la fase SQL en curso
# ---------------------------------------------------------------------------

def _res_sql(pregunta: str) -> ri.ResultadoInstacart:
    return ri.ResultadoInstacart(pregunta=pregunta, sql="SELECT COUNT(*) AS n FROM orders",
                                 columnas=["n"], filas=[(21,)], intentos=1)


def _estado_pendiente(at: AppTest, pregunta: str, en_curso: dict) -> None:
    at.session_state["pregunta_en_curso"] = pregunta
    at.session_state["vista"] = "chat"
    at.session_state[app_instacart.CLAVE_SQL_EN_CURSO] = en_curso


def test_regreso_al_chat_reutiliza_la_fase_sql_ya_calculada(app):
    """Estado que deja un hilo abandonado que SÍ terminó la fase SQL: al
    volver al chat no se llama otra vez al modelo para generar SQL."""
    pregunta = "¿Cuántos pedidos hay?"
    modelo = ModeloSimulado([], ["Hay 21 pedidos en total."])
    at = app(modelo)
    hilo_terminado = threading.Thread(target=lambda: None)
    hilo_terminado.start()
    hilo_terminado.join()
    _estado_pendiente(at, pregunta, {"pregunta": pregunta, "hilo": hilo_terminado, "res": _res_sql(pregunta)})
    at.run()

    assert not list(at.exception)
    assert modelo.prompts_sql == [], "la fase SQL ya calculada no debe repetirse"
    assert "Hay 21 pedidos en total." in _textos(at)
    assert [m["rol"] for m in at.session_state["historial"]] == ["usuario", "asistente"]
    assert at.session_state[app_instacart.CLAVE_SQL_EN_CURSO] is None
    assert at.session_state["pregunta_en_curso"] is None


def test_regreso_al_chat_espera_al_hilo_que_todavia_calcula(app):
    """Estado que deja un hilo abandonado que TODAVÍA calcula: se espera a
    ese hilo en vez de lanzar otro cálculo (antes: una llamada extra a
    Ollama por cada cambio de vista)."""
    pregunta = "¿Cuántos pedidos hay?"
    modelo = ModeloSimulado([], ["Hay 21 pedidos en total."])
    at = app(modelo)
    en_curso = {"pregunta": pregunta, "hilo": None, "res": None}

    def _calculo_lento():
        time.sleep(0.6)
        en_curso["res"] = _res_sql(pregunta)

    en_curso["hilo"] = threading.Thread(target=_calculo_lento, daemon=True)
    en_curso["hilo"].start()
    _estado_pendiente(at, pregunta, en_curso)
    t0 = time.perf_counter()
    at.run()

    assert not list(at.exception)
    assert time.perf_counter() - t0 >= 0.5, "no esperó al hilo que calculaba"
    assert modelo.prompts_sql == []
    assert "Hay 21 pedidos en total." in _textos(at)
    assert len(at.session_state["historial"]) == 2


def test_hilo_muerto_sin_resultado_o_pregunta_distinta_si_calculan(app):
    """Si el hilo murió sin dejar resultado, o el resultado guardado es de
    otra pregunta, sí se calcula (una sola vez)."""
    pregunta = "¿Cuántos pedidos hay?"
    modelo = ModeloSimulado(["SELECT COUNT(*) AS n FROM orders"], ["Hay 21 pedidos en total."])
    at = app(modelo)
    muerto = threading.Thread(target=lambda: None)
    muerto.start()
    muerto.join()
    _estado_pendiente(at, pregunta, {"pregunta": "otra pregunta", "hilo": muerto, "res": _res_sql("otra")})
    at.run()
    assert len(modelo.prompts_sql) == 1
    assert len(at.session_state["historial"]) == 2


@pytest.mark.parametrize("nid", [f"N{i}" for i in range(1, 12)])
def test_cada_fijado_navega_ida_y_vuelta(app, nid):
    at = _preguntar(app(ModeloSimulado(
        ["SELECT COUNT(*) AS n FROM orders"], ["Hay 21 pedidos en total."],
    )), "¿Cuántos pedidos hay?")
    titulo = f"{nid} — {app_instacart.TITULOS_NECESIDADES[nid]}"

    at.sidebar.button(key=f"fijado_{nid}").click().run()
    assert not list(at.exception), f"{nid}: {list(at.exception)}"
    assert any(t.value == titulo for t in at.title)

    at.button(key="vista_chat").click().run()
    assert _en_chat(at) and len(at.chat_message) == 2

    at.button(key="vista_dashboard").click().run()
    assert not list(at.exception)
    assert any(t.value == titulo for t in at.title), f"{nid}: al volver no se conservó la necesidad"
