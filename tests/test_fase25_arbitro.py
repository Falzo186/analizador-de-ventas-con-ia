"""Fase 25: el chequeo automático contra la referencia es el árbitro del
veredicto en autocorrección; el juez-LLM solo diagnostica los intentos que ya
fallaron, y su opinión se registra aparte (`src.llm.iteraciones_juez`). Mismo
modelo simulado y fixture chica que la Fase 11 (sin Ollama).

En la fixture, N5 tiene referencia (Fase 28: la N1 nueva queda vacía en la fixture): su SQL de referencia acierta y
`SQL_OK` ("SELECT COUNT(*) AS n FROM orders") falla.
"""

from __future__ import annotations

import json

import pytest

from src.analytics import necesidades as nec
from src.llm import iteraciones_juez as ij
from src.llm import prompts_instacart as pi
from tests.test_fase11_iteraciones import (  # noqa: F401 - fixtures
    SQL_OK,
    ModeloIteraciones,
    _a_dashboard,
    _juez,
    app,
    parchear,
    rutas,
)

SQL_N5 = nec.DEFINICIONES["N5"].sql()


def _diag(diagnostico: str, comparacion: str = "difiere") -> str:
    return json.dumps({"comparacion": comparacion, "diagnostico_enfoque": diagnostico}, ensure_ascii=False)


def test_por_defecto_autocorreccion_usa_el_chequeo_e_independiente_el_juez():
    assert ij.arbitro_por_defecto(ij.MODO_AUTOCORRECCION) == ij.ARBITRO_CHEQUEO
    assert ij.arbitro_por_defecto(ij.MODO_INDEPENDIENTE) == ij.ARBITRO_JUEZ


def test_intento_correcto_lo_decide_el_chequeo_aunque_el_juez_diga_que_no(parchear):
    modelo = parchear(ModeloIteraciones(sql=[SQL_N5], juez=[_juez("incorrecta", "no me convence")]))
    it = ij.correr_iteraciones("N5", modelos=["chico"], n=1).iteraciones[0]

    assert it.arbitro == ij.ARBITRO_CHEQUEO and it.coincide_referencia
    assert (it.veredicto, it.origen_veredicto) == (ij.CORRECTA, ij.ORIGEN_CHEQUEO)
    assert it.justificacion == it.detalle_coincidencia
    # La opinión del juez queda aparte, solo como dato.
    assert (it.juez_veredicto, it.juez_justificacion, it.juez_origen) == (ij.INCORRECTA, "no me convence", ij.ORIGEN_JUEZ)
    # Acertó: no hay nada que diagnosticar.
    assert not modelo.de_tipo("diagnostico") and it.diagnostico is None and it.diagnostico_retro == ""


def test_intento_fallido_lo_decide_el_chequeo_y_el_juez_solo_diagnostica(parchear):
    modelo = parchear(ModeloIteraciones(
        juez=[_juez("correcta", "todo bien")], diagnostico=[_diag("Cuentas pedidos en vez de líneas por producto.")],
    ))
    it = ij.correr_iteraciones("N5", modelos=["chico"], n=1).iteraciones[0]

    assert not it.coincide_referencia
    assert (it.veredicto, it.origen_veredicto) == (ij.INCORRECTA, ij.ORIGEN_CHEQUEO), "el «correcta» del juez no cuenta"
    assert it.juez_veredicto == ij.CORRECTA
    assert it.diagnostico == it.diagnostico_retro == "Cuentas pedidos en vez de líneas por producto."
    assert it.diagnostico_comparacion == "difiere"
    # La llamada de diagnóstico ya sabe que el intento está mal y no pide veredicto.
    (llamada,) = modelo.de_tipo("diagnostico")
    assert "CHEQUEO AUTOMÁTICO (ya decidido: INCORRECTO)" in llamada["usuario"]
    assert llamada["temperatura"] == ij.TEMPERATURA_JUEZ and llamada["formato_json"]


def test_prompt_de_diagnostico_no_pide_veredicto():
    sistema, usuario = pi.prompt_diagnostico_instacart(
        "p", "ref", "resp", sql_generado="SELECT 1", resultado_generado="a\n1", detalle_chequeo="no coincide")
    assert "YA DETERMINÓ" in sistema and '"veredicto"' not in sistema and "diagnostico_enfoque" in sistema
    assert usuario.endswith("CHEQUEO AUTOMÁTICO (ya decidido: INCORRECTO): no coincide")


def test_sin_medir_juez_no_se_llama_al_juez_salvo_para_diagnosticar(parchear):
    modelo = parchear(ModeloIteraciones(sql=[SQL_N5, SQL_OK]))
    corrida = ij.correr_iteraciones("N5", modelos=["chico"], n=2, medir_juez=False, corta_al_acertar=False)
    acierto, fallo = corrida.iteraciones
    assert acierto.veredicto == ij.CORRECTA and fallo.veredicto == ij.INCORRECTA
    assert not modelo.de_tipo("juez") and acierto.juez_veredicto is None
    assert len(modelo.de_tipo("diagnostico")) == 1 and fallo.diagnostico == "Revisa la métrica."


def test_pipeline_fallido_no_llama_a_ningun_juez(parchear):
    modelo = parchear(ModeloIteraciones(sql=["DELETE FROM orders"]))
    it = ij.correr_iteraciones("N5", modelos=["chico"], n=1).iteraciones[0]
    assert not it.ok and it.veredicto == ij.INCORRECTA and it.origen_veredicto == ij.ORIGEN_AUTOMATICO
    assert not modelo.de_tipo("juez") and not modelo.de_tipo("diagnostico")


def test_retroalimentacion_con_el_chequeo_como_arbitro(parchear):
    parchear(ModeloIteraciones(sql=[SQL_OK, SQL_N5, SQL_OK], diagnostico=[_diag("Falta unir con products.")]))
    corrida = ij.correr_iteraciones("N5", modelos=["chico"], n=3, corta_al_acertar=False)
    fallo, acierto, despues = corrida.iteraciones

    assert acierto.retroalimentacion == ij.texto_retroalimentacion(fallo)
    assert "Un chequeo automático comparó su resultado con el de la respuesta correcta" in acierto.retroalimentacion
    assert "Veredicto (chequeo automático contra la respuesta correcta): INCORRECTA." in acierto.retroalimentacion
    assert "Qué está mal en el enfoque, según el evaluador: Falta unir con products." in acierto.retroalimentacion
    # Tras un acierto: el veredicto y "conserva el enfoque", sin diagnóstico ni el detalle del chequeo.
    retro = despues.retroalimentacion
    assert "Veredicto (chequeo automático contra la respuesta correcta): CORRECTA." in retro
    assert retro.endswith("Tu resultado coincide con la respuesta correcta: conserva el enfoque.")
    assert acierto.detalle_coincidencia not in retro and "enfoque, según" not in retro


def test_resumen_mide_cuanto_se_equivoca_el_juez(parchear):
    # 4 intentos: acierto/juez sí, acierto/juez no (falso negativo), fallo/juez sí (falso positivo), fallo/juez no.
    parchear(ModeloIteraciones(
        sql=[SQL_N5, SQL_N5, SQL_OK, SQL_OK],
        juez=[_juez("correcta"), _juez("parcialmente correcta"), _juez("correcta"), _juez("incorrecta")],
    ))
    corrida = ij.correr_iteraciones("N5", modelos=["chico"], n=4, modo=ij.MODO_AUTOCORRECCION,
                                    corta_al_acertar=False)
    r = corrida.resumen()["chico"]
    assert [it.veredicto for it in corrida.iteraciones] == [ij.CORRECTA, ij.CORRECTA, ij.INCORRECTA, ij.INCORRECTA]
    assert (r.correctas, r.incorrectas, r.primer_correcto) == (2, 2, 1)
    assert (r.juez_opiniones, r.juez_correctas, r.juez_acuerdos) == (4, 2, 2)
    assert (r.juez_falsos_negativos, r.juez_falsos_positivos) == (1, 1)
    texto = ij.a_texto(corrida)
    assert "Árbitro del veredicto: chequeo automático" in texto
    assert "Opinión del juez-LLM (solo comparación, no decide): parcialmente correcta" in texto
    assert "juez-LLM: 2/4 «correcta», de acuerdo con el chequeo en 2/4 (1 falsos negativos, 1 falsos positivos)" in texto
    doc = json.loads(ij.a_json(corrida))
    assert doc["arbitro"] == ij.ARBITRO_CHEQUEO and doc["iteraciones"][1]["juez_veredicto"] == ij.PARCIAL


def test_modo_independiente_no_cambia_el_juez_decide(parchear):
    modelo = parchear(ModeloIteraciones(sql=[SQL_N5], juez=[_juez("parcialmente correcta", "le falta algo")]))
    corrida = ij.correr_iteraciones("N5", modelos=["chico"], n=2, modo=ij.MODO_INDEPENDIENTE)
    assert corrida.arbitro == ij.ARBITRO_JUEZ
    for it in corrida.iteraciones:
        assert (it.veredicto, it.origen_veredicto) == (ij.PARCIAL, ij.ORIGEN_JUEZ)
        assert it.coincide_referencia and it.retroalimentacion is None
    assert len(modelo.de_tipo("juez")) == 2 and not modelo.de_tipo("diagnostico")


def test_arbitro_juez_en_autocorreccion_reproduce_la_fase_24(parchear):
    parchear(ModeloIteraciones(sql=[SQL_N5], juez=[_juez("incorrecta", "no", "Ordena distinto.")]))
    corrida = ij.correr_iteraciones("N5", modelos=["chico"], n=2, arbitro=ij.ARBITRO_JUEZ)
    primero, segundo = corrida.iteraciones
    assert primero.coincide_referencia and primero.veredicto == ij.INCORRECTA, "con árbitro juez, decide el juez"
    assert "Veredicto del evaluador: INCORRECTA." in segundo.retroalimentacion
    assert "según el evaluador: Ordena distinto." in segundo.retroalimentacion


@pytest.mark.parametrize("crudo,diagnostico,origen", [
    (_diag("Falta el JOIN."), "Falta el JOIN.", ij.ORIGEN_JUEZ),
    ('texto antes {"comparacion": "x", "diagnostico_enfoque": "Usa COUNT."} y después', "Usa COUNT.", ij.ORIGEN_JUEZ),
    ('{"comparacion": "x"}', None, ij.ORIGEN_JUEZ_INVALIDO),
    ("no es json", None, ij.ORIGEN_JUEZ_INVALIDO),
])
def test_parsear_diagnostico(crudo, diagnostico, origen):
    d = ij.parsear_diagnostico(crudo)
    assert (d.diagnostico, d.origen) == (diagnostico, origen)


def test_arbitro_desconocido_se_rechaza():
    with pytest.raises(ValueError):
        ij.nueva_corrida("N5", modelos=["chico"], n=1, arbitro="otro")


def test_cli_acepta_arbitro_y_sin_medir_juez(parchear, tmp_path):
    modelo = parchear(ModeloIteraciones(sql=[SQL_N5]))
    assert ij.main(["N5", "--iteraciones", "1", "--pequeno", "chico", "--grande", "-", "--arbitro", "chequeo",
                    "--sin-medir-juez", "--out", str(tmp_path)]) == 0
    doc = json.loads((tmp_path / "N5_iteraciones_juez.json").read_text(encoding="utf-8"))
    assert doc["arbitro"] == "chequeo" and doc["medir_juez"] is False
    assert doc["iteraciones"][0]["veredicto"] == ij.CORRECTA and not modelo.de_tipo("juez")


def test_panel_muestra_el_veredicto_del_chequeo_y_la_opinion_del_juez_aparte(app):
    from src.ui import app_instacart

    modelo = ModeloIteraciones(sql=[SQL_OK, SQL_N5], juez=[_juez("incorrecta", "no me convence")],
                               diagnostico=[_diag("Falta unir con products.")])
    at = app(modelo)
    at.number_input(key=app_instacart.CLAVE_ITERACIONES).set_value(2).run()
    at = _a_dashboard(at, "N5")
    at.button(key="iterar_N5").click().run()
    assert not list(at.exception), list(at.exception)

    textos = "\n".join(m.value for m in at.markdown)
    assert textos.count("**Veredicto — chequeo automático contra la referencia**") == 4  # 2 intentos x 2 modelos
    assert "✅ correcta — " in textos and "❌ incorrecta — " in textos
    captions = "\n".join(c.value for c in at.caption)
    assert captions.count("⚖️ Opinión del juez-LLM (solo comparación, no decide nada): ❌ incorrecta — no me convence") == 4
    assert "⚖️ Juez-LLM (solo comparación): 0 de 2 «correcta»; de acuerdo con el chequeo en 1 de 2" in captions
    assert textos.count("**Diagnóstico del enfoque** (lo que recibe el intento siguiente): Falta unir con products.") == 2
