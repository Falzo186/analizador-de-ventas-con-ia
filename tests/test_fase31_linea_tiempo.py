"""Fase 31: botones de las 10 necesidades, línea de tiempo por intento,
veredicto con «por qué» y bitácora sana. AppTest y modelos falsos (sin Ollama)."""
from __future__ import annotations

import pytest

from src.analytics import registro
from src.analytics.necesidades import DEFINICIONES
from src.llm import bitacora_chat as bc
from src.llm import iteraciones_juez as ij
from src.llm import responder_instacart as ri
from src.llm import retroalimentacion as rf
from src.ui import app_instacart, linea_tiempo
from tests.test_app_instacart import ModeloSimulado, app, rutas  # noqa: F401 - fixtures de la app
from tests.test_fase27_bitacora import SQL_BUENO, SQL_MALO, Doble, _preguntar, _sospechoso, db  # noqa: F401

REF_N2 = [{"department": "dairy eggs", "veces_comprado": 5631067, "recompras": 3773723, "tasa_recompra": 0.6702},
          {"department": "beverages", "veces_comprado": 1, "recompras": 1, "tasa_recompra": 0.654}]


def _iteracion(numero: int, veredicto: str, **k) -> ij.Iteracion:
    base = dict(modelo="m", numero=numero, sql=f"SELECT {numero}", respuesta=f"respuesta {numero}", error=None,
                intentos_sql=1, origen_respuesta="modelo", veredicto=veredicto, justificacion="j",
                origen_veredicto=ij.ORIGEN_CHEQUEO, crudo_juez=None, duracion_pipeline_s=3.0, duracion_juez_s=1.5,
                total=2, columnas=["department", "tasa"], filas=[["pets", 0.595]], n_filas=1,
                arbitro=ij.ARBITRO_CHEQUEO, tiempos={"generacion_sql_s": 2.0, "ejecucion_sql_s": 0.3,
                                                      "redaccion_s": 0.7})
    base.update(k)
    return ij.Iteracion(**base)


def _corrida_simulada() -> ij.CorridaIteraciones:
    c = ij.CorridaIteraciones(necesidad="N2", pregunta=DEFINICIONES["N2"].pregunta, referencia="ref", modelos=["m"],
                              n_iteraciones=2, modo=ij.MODO_AUTOCORRECCION, arbitro=ij.ARBITRO_CHEQUEO,
                              corta_al_acertar=True, referencia_filas=REF_N2, estado=ij.ESTADO_COMPLETA)
    primero = _iteracion(1, ij.INCORRECTA, error="No se pudo ejecutar la consulta contra los datos: Binder Error: x",
                         columnas=None, filas=None, n_filas=None, respuesta=None,
                         explicacion_usuario="No coincide. Obtuvo: pets 0.595; esperado: dairy eggs 0.6702.",
                         diagnostico="agrupa por departamento", juez_veredicto=ij.CORRECTA, juez_justificacion="ok",
                         pista_modelo="PISTA SANEADA DEL INTENTO 1")
    segundo = _iteracion(2, ij.CORRECTA, retroalimentacion="PISTA SANEADA DEL INTENTO 1",
                         explicacion_usuario="Coincide con la referencia.")
    c.iteraciones = [primero, segundo]
    c.convergencia = {"m": 2}
    return c


# ---------------------------------------------------------------------------
# Paso 1: los 10 botones
# ---------------------------------------------------------------------------

def test_existen_10_botones_con_la_pregunta_oficial_como_ayuda(app):  # noqa: F811
    at = app(ModeloSimulado([], []))
    botones = {b.key: b for b in at.button if (b.key or "").startswith("rapido_")}
    assert sorted(botones, key=lambda k: int(k.split("_N")[1])) == [f"rapido_N{i}" for i in range(1, 11)]
    for nid in DEFINICIONES:
        b = botones[f"rapido_{nid}"]
        assert b.label == f"{nid} · {registro.definicion(nid).titulo}"
        assert b.proto.help == DEFINICIONES[nid].pregunta


@pytest.mark.parametrize("nid", ["N2", "N9"])
def test_cada_clic_manda_el_texto_oficial_con_su_necesidad(app, monkeypatch, nid):  # noqa: F811
    llamadas = []

    def nueva(necesidad, **k):
        llamadas.append((necesidad, k))
        c = _corrida_simulada()
        c.necesidad, c.pregunta = necesidad, ij.PREGUNTAS[necesidad]
        c.iteraciones, c.convergencia = [], {}
        return c

    monkeypatch.setattr(ij, "nueva_corrida", nueva)
    monkeypatch.setattr(ij, "correr_iteraciones", lambda *a, **k: k["corrida"])
    at = app(ModeloSimulado([], []))
    at.button(key=f"rapido_{nid}").click().run()
    assert not list(at.exception), list(at.exception)
    (necesidad, k), = llamadas
    assert necesidad == nid and k["modelos"] == ["qwen2.5-coder:1.5b"], "con la configuración actual del chat"
    usuario = at.session_state["historial"][-2]
    assert usuario["contenido"] == DEFINICIONES[nid].pregunta and usuario["metadatos"]["necesidad"] == nid
    assert at.session_state["historial"][-1]["tipo"] == app_instacart.TIPO_CORRIDA
    assert at.session_state[app_instacart.CLAVE_NECESIDAD_RAPIDA] is None, "se limpia al terminar"


def test_con_ollama_caido_el_boton_avisa_sin_romper(app, monkeypatch):  # noqa: F811
    from src.llm import client

    at = app(ModeloSimulado([], []))

    def _caido():
        raise client.LlmNoDisponible("sin conexión")

    monkeypatch.setattr(client, "modelos_disponibles", _caido)
    at.button(key="rapido_N5").click().run()
    assert not list(at.exception)
    assert any("Ollama no responde" in e.value for e in at.error)


# ---------------------------------------------------------------------------
# Pasos 2-3: línea de tiempo y veredicto
# ---------------------------------------------------------------------------

def test_linea_de_tiempo_de_boton_muestra_todos_los_campos(app, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ij, "nueva_corrida", lambda necesidad, **k: _corrida_simulada())
    monkeypatch.setattr(ij, "correr_iteraciones", lambda *a, **k: k["corrida"])
    at = app(ModeloSimulado([], []))
    at.button(key="rapido_N2").click().run()
    md = "\n".join(m.value for m in at.markdown)
    caps = "\n".join(c.value for c in at.caption)
    codigos = [c.value for c in at.code]
    # a) encabezado con modelo, técnica y tiempos (intento, juez y por fase)
    assert "Intento 1 de 2 · m · base" in md and "⏱️ intento 3.0 s · juez 1.5 s" in md
    assert "Por fase: generación de SQL 2.0 s · ejecución 0.3 s · redacción 0.7 s" in caps
    # b) pregunta y pista recibida (texto exacto)
    assert f"**Pregunta enviada:** {DEFINICIONES['N2'].pregunta}" in md
    assert "Pista que recibió del intento anterior" in md and "PISTA SANEADA DEL INTENTO 1" in codigos
    # c) SQL, d) error exacto o resultado, e) respuesta
    assert "SELECT 1" in codigos and "SELECT 2" in codigos
    assert "No se pudo ejecutar la consulta contra los datos: Binder Error: x" in codigos
    assert "**Respuesta de la IA:** respuesta 2" in md
    # f) evaluación con el por qué (puede traer valores de la referencia: es para el usuario)
    assert "**Evaluación:** ❌ Incorrecta" in md and "**Evaluación:** ✅ Correcta" in md
    assert "Por qué: No coincide. Obtuvo: pets 0.595; esperado: dairy eggs 0.6702." in caps
    # g) juez rotulado como diagnóstico y discrepancia con el chequeo
    assert "diagnóstico (no decide el veredicto)" in caps
    assert any("El juez no coincide con el chequeo automático" in w.value for w in at.warning)
    # h) pista al siguiente y «ya converge»
    assert "sin pista: ya converge" in caps
    # cierre con totales y regla de «parcial»
    assert any("Resultado final: aceptada en el intento 2" in s.value for s in at.success)
    assert "Tiempo total del modelo: 6.0 s · tiempo total del juez: 3.0 s" in caps
    assert rf.REGLA_PARCIAL in caps


def test_boton_muestra_correcta_parcial_e_incorrecta_segun_el_veredicto():
    for veredicto, etiqueta in ((ij.CORRECTA, "Correcta"), (ij.PARCIAL, "Parcialmente correcta"),
                                (ij.INCORRECTA, "Incorrecta")):
        v = linea_tiempo.vista_iteracion(_iteracion(1, veredicto), "p")
        assert v["evaluacion"]["etiqueta"] == etiqueta


def test_cierre_rechazada_explica_el_mejor_intento_y_la_evolucion():
    c = _corrida_simulada()
    c.iteraciones[1].veredicto = ij.INCORRECTA
    c.convergencia = {}
    cierre = ij.cierre_de(c, "m")
    assert cierre.aceptada_en is None and cierre.texto == "Resultado final: rechazada tras 2 intento(s)"
    assert "Mejor intento: el 2" in cierre.explicacion and "Evolución: 1:incorrecta → 2:incorrecta" in cierre.explicacion


def test_chat_libre_nunca_dice_correcta(app):  # noqa: F811
    at = app(ModeloSimulado(["SELECT COUNT(*) AS n FROM orders"], ["Hay 21 pedidos."]))
    at.chat_input(key="chat_input").set_value("¿Cuántos pedidos hay?").run()
    assert not list(at.exception)
    textos = "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption])
    assert "**Evaluación:** ✅ SQL sano" in textos and linea_tiempo.AVISO_SIN_REFERENCIA in textos
    assert "Correcta" not in textos and "correcta" not in textos.replace("es la correcta", "")


def test_chat_libre_error_y_sospechoso():
    assert linea_tiempo.evaluacion_chat({"ok": False, "tipo_error": "error", "error": "Binder"})["etiqueta"] \
        == "Error de ejecución"
    assert linea_tiempo.evaluacion_chat({"ok": True, "revision_sql": {"sospechoso": True, "motivo": "raro"}})[
        "etiqueta"] == "SQL sospechoso"


# ---------------------------------------------------------------------------
# Solo pista_modelo viaja al prompt (no-fuga, enfoque de la Fase 24)
# ---------------------------------------------------------------------------

def test_solo_la_pista_saneada_llega_al_siguiente_intento(monkeypatch):
    recibidas = []
    respuestas = iter([[("pets", 0.595)], [("dairy eggs", 0.6702)]])

    def pipeline(pregunta, **kwargs):
        recibidas.append(kwargs.get("retroalimentacion"))
        return ri.ResultadoInstacart(pregunta=pregunta, sql="SELECT 1", columnas=["department", "tasa"],
                                     filas=next(respuestas), respuesta="r", tecnica=kwargs["tecnica"],
                                     tiempos={"generacion_sql_s": 1.0})

    monkeypatch.setattr(ri, "responder_pregunta_instacart", pipeline)
    monkeypatch.setattr(ij, "diagnosticar", lambda *a, **k: ij.Diagnostico(
        "La correcta es dairy eggs con 0.6702", "El departamento correcto es dairy eggs. Agrupa por departamento.",
        "juez", "{}"))
    c = ij.CorridaIteraciones(necesidad="N2", pregunta=DEFINICIONES["N2"].pregunta, referencia="ref", modelos=["m"],
                              n_iteraciones=2, modo=ij.MODO_AUTOCORRECCION, arbitro=ij.ARBITRO_CHEQUEO,
                              medir_juez=False, corta_al_acertar=True, referencia_filas=REF_N2)
    ij.correr_iteraciones("N2", modelos=["m"], corrida=c)
    primero, segundo = c.iteraciones
    vp = rf.valores_protegidos(c.pregunta, c.referencia_filas)
    assert recibidas == [None, primero.pista_modelo], "lo que viaja es EXACTAMENTE pista_modelo"
    assert rf.fugas(primero.pista_modelo, vp) == [], "la pista no trae valores de la referencia"
    assert rf.fugas(primero.explicacion_usuario, vp), "la explicación sí (es para el usuario)"
    assert primero.explicacion_usuario not in (recibidas[1] or "")
    assert "dairy eggs" in primero.explicacion_usuario and segundo.veredicto == ij.CORRECTA
    assert primero.tiempos == {"generacion_sql_s": 1.0}


# ---------------------------------------------------------------------------
# Paso 4: bitácora sana
# ---------------------------------------------------------------------------

def test_alarma_del_revisor_sin_efecto_no_guarda_leccion(db, monkeypatch):  # noqa: F811
    bitacora = bc.Bitacora()
    doble = Doble([SQL_BUENO, SQL_BUENO + " "], [_sospechoso("Algo raro.", "Lección rara.")])
    res = _preguntar(db, monkeypatch, doble, "¿Pasillos por departamento?", bitacora=bitacora, revisar_sql=True)
    assert res.ok and res.reintento_por_revision and res.revision_sql["resultado_cambio"] is False
    assert res.revision_sql["alarma_sin_efecto"] is True
    assert bitacora.cargar() == [] and res.lecciones_nuevas == []
    assert bitacora.estadisticas() == {"alarmas_sin_efecto": 1, "alarmas_con_efecto": 0}


def test_error_de_duckdb_si_guarda_leccion(db, monkeypatch):  # noqa: F811
    bitacora = bc.Bitacora()
    _preguntar(db, monkeypatch, Doble([SQL_MALO, SQL_BUENO]), "¿Pasillos por departamento?", bitacora=bitacora)
    (x,) = bitacora.cargar()
    assert x.origen == bc.ORIGEN_ERROR and x.firma == "columna_en_tabla:aisles.department_id"


def test_corrida_de_boton_solo_aprende_de_incorrectas_que_fallaron_en_la_bd(tmp_path):
    b = bc.Bitacora(tmp_path / "b.json")
    c = _corrida_simulada()
    c.iteraciones.append(_iteracion(3, ij.INCORRECTA, error="El modelo tardó demasiado en generar la consulta"))
    c.iteraciones[0].error = 'No se pudo ejecutar la consulta contra los datos: Binder Error: Table "a" does not ' \
                             'have a column named "department_id"'
    c.iteraciones[0].sql = "SELECT a.department_id FROM aisles a"
    nuevas = bc.lecciones_de_corrida(c, b, {"aisles": ["aisle_id", "aisle"], "products": ["department_id"]})
    assert len(nuevas) == 1 and len(b.cargar()) == 1, "ni la correcta ni el error del modelo enseñan algo"


def test_maximo_3_lecciones_relevantes_y_sin_duplicados(tmp_path):
    b = bc.Bitacora(tmp_path / "b.json")
    temas = ["pasillos departamento aisles", "tasa recompra reordered", "promedio pedido carrito",
             "hora pedidos order_hour", "clientes user_id frecuencia"]
    for i, t in enumerate(temas):
        b.agregar(bc.Leccion(f"f{i}", f"Lección sobre {t}", bc.ORIGEN_ERROR, f"pregunta de {t}", "s", "d"))
    b.agregar(bc.Leccion("otra", "Lección sobre pasillos departamento aisles", bc.ORIGEN_ERROR, "p", "s", "d"))
    assert len(b.cargar()) == 5, "casi igual a una existente: se suma a ella"
    elegidas = b.para_prompt("¿Qué departamento tiene más pasillos?")
    assert 1 <= len(elegidas) <= bc.MAX_LECCIONES_PROMPT == 3
    assert elegidas[0].firma == "f0"
    assert b.para_prompt("¿Cuál es la capital de Francia?") == []
    # Efecto en el tamaño del prompt: el bloque relevante es más chico que el de antes (todas por prioridad).
    assert len(b.bloque_prompt("¿Qué departamento tiene más pasillos?")) < len(
        "\n".join([bc.ENCABEZADO_PROMPT, *(f"- {x.leccion}" for x in b.cargar())]))


def test_ui_borrar_una_leccion_y_limpiar_con_confirmacion(app):  # noqa: F811
    for i, t in enumerate(["pasillos departamento", "tasa recompra"]):
        bc.Bitacora().agregar(bc.Leccion(f"f{i}", f"Lección {t}", bc.ORIGEN_ERROR, f"p {t}", "s", "d"))
    at = app(ModeloSimulado([], []))
    assert any(e.label.startswith("📓 Bitácora de aprendizaje (2") for e in at.expander)
    at.button(key="borrar_leccion_0").click().run()
    assert len(bc.Bitacora().cargar()) == 1
    assert at.button(key="limpiar_bitacora").disabled, "sin confirmar no se puede limpiar"
    at.checkbox(key="confirmar_limpiar_bitacora").check().run()
    at.button(key="limpiar_bitacora").click().run()
    assert bc.Bitacora().cargar() == []


# ---------------------------------------------------------------------------
# Regresión: lo que va al modelo no cambió en la Fase 31
# ---------------------------------------------------------------------------

def test_prompt_de_cada_tecnica_identico_al_de_antes_de_la_fase_31():
    """Golden generado con el código del commit 977818a (antes de la Fase 31)."""
    import json
    from pathlib import Path

    from src.llm import tecnicas
    from src.llm.prompts_instacart import prompt_sql_instacart

    golden = json.loads(Path("tests/data/prompt_tecnicas_golden.json").read_text(encoding="utf-8"))
    esquema = "ESQUEMA DE PRUEBA\n- orders: order_id INTEGER"
    for clave, esperado in golden.items():
        nid, tecnica = clave.split("|")
        pregunta = DEFINICIONES[nid].pregunta
        bloque = tecnicas.contexto_de_tecnica(tecnica, pregunta).bloque
        assert list(prompt_sql_instacart(pregunta, esquema, None, "RETRO", "LECCIONES", bloque)) == esperado, clave
