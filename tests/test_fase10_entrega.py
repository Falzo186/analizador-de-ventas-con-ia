"""Fase 10 (entregable de curso): JSON individual por necesidad, del
software determinista (Parte A) y de la IA local (Parte B), para las 5
necesidades seleccionadas. Todo contra la fixture chica y con el modelo
simulado (sin Ollama).

Contenido de una descarga: `AppTest` no expone los bytes de un
`st.download_button`, pero su URL es el sha224 de (bytes, mime, nombre de
archivo) (`MemoryMediaFileStorage`). Comparar la URL con la calculada sobre
los bytes esperados comprueba que se sirve ese contenido exacto.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from streamlit.runtime.memory_media_file_storage import _calculate_file_id
from streamlit.testing.v1 import AppTest

from src.analytics.registro import NECESIDADES
from src.dashboard import servicio
from src.llm import client
from src.llm import formato_entrega as fe
from src.llm import responder_instacart as ri
from src.pdf import generador as pdf_generador
from src.ui import app_instacart
from tests.helpers_analytics import construir_fixture
from tests.test_app_instacart import ModeloSimulado

ENTREGA = ("N1", "N2", "N4", "N9", "N11")
OTRAS = sorted(set(NECESIDADES) - set(ENTREGA))
MIME_JSON = "application/json"


def _url(contenido: bytes, nombre: str) -> str:
    return _calculate_file_id(contenido, MIME_JSON, nombre)


def _descargas(at: AppTest) -> list:
    return list(at.get("download_button"))


@pytest.fixture(scope="module")
def rutas(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fase10")
    src, agg = construir_fixture(tmp)
    return src, agg, tmp


@pytest.fixture()
def parchear(rutas, monkeypatch):
    src, agg, tmp = rutas
    for modulo in (servicio, pdf_generador):
        monkeypatch.setattr(modulo, "DB_PATH_DEFAULT", src)
        monkeypatch.setattr(modulo, "AGG_PATH_DEFAULT", agg)
        monkeypatch.setattr(modulo, "OUT_DIR_DEFAULT", tmp / "referencia")
    monkeypatch.setattr(pdf_generador, "OUT_PDF_DEFAULT", tmp / "pdf" / "reporte.pdf")
    monkeypatch.setattr(ri, "DB_PATH_DEFAULT", src)
    monkeypatch.setattr(client, "modelos_disponibles", lambda: ["qwen2.5-coder:1.5b"])
    monkeypatch.setattr(client, "modelos_cargados", lambda: [])
    return tmp / "referencia"


def test_la_seleccion_es_la_de_la_entrega():
    assert servicio.NECESIDADES_ENTREGA == ENTREGA
    assert tuple(fe.PREGUNTAS_ENTREGA) == ENTREGA


# ---------------------------------------------------------------------------
# Parte A: JSON determinista
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nid", ENTREGA)
def test_json_para_descarga_es_el_archivo_en_disco(parchear, nid):
    descarga = servicio.json_para_descarga(nid)
    ruta = parchear / f"{nid}.json"
    assert descarga.nombre_archivo == f"{nid}.json"
    assert descarga.contenido == ruta.read_bytes()
    data = json.loads(descarga.contenido)
    for campo in ("pregunta", "decision", "valores_clave", "hallazgos", "consultas"):
        assert campo in data, f"{nid}.json no tiene {campo}"
    assert data["id"] == nid


def test_json_rancio_se_recalcula_antes_de_servirlo(parchear):
    servicio.json_para_descarga("N4")
    ruta = parchear / "N4.json"
    data = json.loads(ruta.read_text(encoding="utf-8"))
    data["huella_bd"]["orders_total"] += 1
    ruta.write_text(json.dumps(data), encoding="utf-8")

    descarga = servicio.json_para_descarga("N4")
    assert not descarga.desde_cache
    assert json.loads(descarga.contenido)["huella_bd"]["orders_total"] == data["huella_bd"]["orders_total"] - 1
    assert descarga.contenido == ruta.read_bytes()
    assert servicio.json_para_descarga("N4").desde_cache


@pytest.fixture()
def dashboard_app(parchear):
    at = AppTest.from_file("src/ui/dashboard.py", default_timeout=90)
    at.run()
    assert not list(at.exception)
    return at


@pytest.mark.parametrize("nid", ENTREGA)
def test_boton_descarga_sirve_el_json_en_disco(dashboard_app, parchear, nid):
    at = dashboard_app
    at.sidebar.radio[0].set_value(nid).run()
    assert not list(at.exception)
    botones = [b for b in _descargas(at) if b.proto.label == "⬇️ Descargar JSON de esta necesidad"]
    assert len(botones) == 1
    contenido = (parchear / f"{nid}.json").read_bytes()
    assert botones[0].proto.url.endswith(_url(contenido, f"{nid}.json") + ".json")


@pytest.mark.parametrize("nid", OTRAS)
def test_otras_necesidades_no_tienen_descarga_individual(dashboard_app, nid):
    dashboard_app.sidebar.radio[0].set_value(nid).run()
    assert not list(dashboard_app.exception)
    assert not [b for b in _descargas(dashboard_app) if "JSON" in b.proto.label]


# ---------------------------------------------------------------------------
# Parte B: JSON de la IA local
# ---------------------------------------------------------------------------

def _instalar_modelo(monkeypatch, modelo: ModeloSimulado) -> None:
    monkeypatch.setattr(client, "chat_con_modelo", modelo.chat_con_modelo)
    monkeypatch.setattr(client, "chat_con_modelo_stream", modelo.chat_con_modelo_stream)


@pytest.mark.parametrize("nid", ENTREGA)
def test_json_ia_tiene_la_estructura_exacta(parchear, monkeypatch, nid):
    modelo = ModeloSimulado(["SELECT COUNT(*) AS n FROM orders", "Hay 21 pedidos."], [])
    _instalar_modelo(monkeypatch, modelo)

    entrega = fe.generar_entrega_ia(nid, modelo="modelo-x")

    assert entrega.ok
    doc = entrega.documento
    assert tuple(doc) == fe.CAMPOS_ENTREGA
    # La pregunta es la de src.analytics (r.pregunta), no un texto reescrito.
    r = servicio.cargar_necesidad(nid).resultado
    assert doc["pregunta"] == r.pregunta
    assert modelo.prompts_sql and r.pregunta in modelo.prompts_sql[0]
    assert doc["necesidad"] == nid
    assert doc["sql_generado"] == "SELECT COUNT(*) AS n FROM orders"
    assert doc["resultado"] == [{"n": 21}]
    assert doc["respuesta_llm"] == "Hay 21 pedidos."
    assert doc["modelo"] == "modelo-x"
    assert datetime.fromisoformat(doc["timestamp"]).tzinfo is not None
    assert json.loads(fe.a_json(doc)) == doc


def test_json_ia_pasa_por_las_guardas_de_seguridad(parchear, monkeypatch):
    _instalar_modelo(monkeypatch, ModeloSimulado(["DELETE FROM orders"], []))
    entrega = fe.generar_entrega_ia("N1", modelo="modelo-x")
    assert not entrega.ok and entrega.documento is None
    assert entrega.res.error.startswith("No puedo ejecutar esa consulta")


def test_json_ia_pasa_por_la_validacion_de_cifras(parchear, monkeypatch):
    # Primera redacción con una cifra que no está en el resultado; el
    # reintento estricto también falla -> plantilla con el valor real.
    _instalar_modelo(monkeypatch, ModeloSimulado(
        ["SELECT COUNT(*) AS n FROM orders", "Hay 99 pedidos.", "Hay 98 pedidos."], [],
    ))
    entrega = fe.generar_entrega_ia("N2", modelo="modelo-x")
    assert entrega.ok and entrega.res.origen_respuesta == "plantilla"
    assert "99" not in entrega.documento["respuesta_llm"]
    assert "21" in entrega.documento["respuesta_llm"]


def test_json_ia_rechaza_necesidades_fuera_de_la_entrega():
    with pytest.raises(ValueError):
        fe.generar_entrega_ia("N3")


@pytest.fixture()
def app_ia(parchear, monkeypatch):
    def _instalar(modelo: ModeloSimulado) -> AppTest:
        _instalar_modelo(monkeypatch, modelo)
        at = AppTest.from_file("src/ui/app_instacart.py", default_timeout=90)
        at.run()
        at.button(key="vista_dashboard").click().run()
        assert not list(at.exception)
        return at

    return _instalar


def test_seccion_de_entrega_tiene_5_botones_y_el_aviso(app_ia):
    at = app_ia(ModeloSimulado([], []))
    assert any(e.label == "📋 Entrega: JSON de la IA para las 5 necesidades" for e in at.expander)
    botones = [b.label for b in at.button if b.label.startswith("Generar JSON de IA")]
    assert botones == [f"Generar JSON de IA para {nid}" for nid in ENTREGA]
    assert any(i.value == app_instacart.AVISO_ENTREGA_IA for i in at.info)


@pytest.mark.parametrize("nid", ENTREGA)
def test_boton_genera_json_ia_y_ofrece_descarga(app_ia, nid):
    at = app_ia(ModeloSimulado(["SELECT COUNT(*) AS n FROM orders", "Hay 21 pedidos."], []))
    at.button(key=f"generar_ia_{nid}").click().run()
    assert not list(at.exception)

    entrega = at.session_state[app_instacart.CLAVE_ENTREGAS_IA][nid]
    assert entrega.ok and entrega.documento["pregunta"] == fe.PREGUNTAS_ENTREGA[nid]
    botones = [b for b in _descargas(at) if b.proto.label == f"⬇️ Descargar {nid}_ia.json"]
    assert len(botones) == 1
    contenido = fe.a_json(entrega.documento).encode("utf-8")
    assert botones[0].proto.url.endswith(_url(contenido, f"{nid}_ia.json") + ".json")
    assert json.loads(contenido)["necesidad"] == nid


def test_error_de_la_ia_se_muestra_sin_ofrecer_descarga(app_ia):
    at = app_ia(ModeloSimulado(["DELETE FROM orders"], []))
    at.button(key="generar_ia_N9").click().run()
    assert not list(at.exception)
    assert any("Consulta rechazada por las guardas de seguridad" in e.value for e in at.error)
    assert not [b for b in _descargas(at) if "_ia.json" in b.proto.label]


def test_el_chat_normal_no_cambia(app_ia):
    at = app_ia(ModeloSimulado(["SELECT COUNT(*) AS n FROM orders"], ["Hay 21 pedidos en total."]))
    at.button(key="vista_chat").click().run()
    assert not any(e.label.startswith("📋 Entrega") for e in at.expander)
    at.chat_input(key="chat_input").set_value("¿Cuántos pedidos hay?").run()
    assert at.session_state["historial"][-1]["contenido"] == "Hay 21 pedidos en total."


# ---------------------------------------------------------------------------
# Parte C: documento petición-necesidad
# ---------------------------------------------------------------------------

def test_documento_de_entrega_reusa_pregunta_y_decision_de_analytics():
    import sys
    from pathlib import Path

    texto = Path("docs/ENTREGA_5_NECESIDADES.md").read_text(encoding="utf-8")
    for nid in ENTREGA:
        modulo = sys.modules[NECESIDADES[nid].__module__]
        assert f"- **Petición:** {modulo.PREGUNTA}\n" in texto, nid
        assert f"- **Necesidad:** {modulo.DECISION}\n" in texto, nid
    for nid in OTRAS:
        assert f"## {nid} " not in texto
