"""Fase 23: panel detallado de iteraciones, fix de intentos perdidos,
export del log y modo de autocorrección encadenada
(`src.llm.iteraciones_juez`, `src.ui.app_instacart`). Mismo modelo simulado
y fixture chica que la Fase 11 (sin Ollama).
"""

from __future__ import annotations

import json

import pytest

from src.llm import iteraciones_juez as ij
from src.llm import prompts_instacart as pi
from src.ui import app_instacart
from tests.test_fase11_iteraciones import (  # noqa: F401 - fixtures
    SQL_OK,
    ModeloIteraciones,
    _a_dashboard,
    _juez,
    app,
    parchear,
    rutas,
)


class _Interrumpir(BaseException):
    """Como el StopException/RerunException de Streamlit: no hereda de
    Exception, así que nada del pipeline lo atrapa."""


# ---------------------------------------------------------------------------
# Fix: se registran exactamente N intentos (incluido el último)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("modo", ij.MODOS)
@pytest.mark.parametrize("n", [1, 2, 3, 5])
@pytest.mark.parametrize("modelos", [["chico"], ["chico", "grande"]])
def test_con_n_iteraciones_se_registran_exactamente_n_por_modelo(parchear, n, modo, modelos):
    parchear(ModeloIteraciones())
    corrida = ij.correr_iteraciones("N3", modelos=modelos, n=n, modo=modo)

    assert len(corrida.iteraciones) == n * len(modelos) == corrida.total_esperado
    for modelo in modelos:
        its = corrida.de_modelo(modelo)
        assert [it.numero for it in its] == list(range(1, n + 1)), "ni uno menos, ni repetidos"
        assert its[-1].numero == n and its[-1].etiqueta == f"Intento {n} de {n} · {modelo}"
        assert all(it.total == n for it in its)
    assert corrida.completa and corrida.estado == ij.ESTADO_COMPLETA
    assert len(json.loads(ij.a_json(corrida))["iteraciones"]) == n * len(modelos)


def test_interrupcion_conserva_los_intentos_terminados(parchear):
    """La causa del intento perdido: la corrida solo se guardaba al terminar
    las N. Ahora cada intento se agrega apenas termina, a una corrida que
    quien llama ya tiene (la UI la guarda en sesión antes de empezar)."""
    parchear(ModeloIteraciones())
    corrida = ij.nueva_corrida("N3", modelos=["chico"], n=5)

    def _cortar(it, hechas, total):
        if hechas == 3:
            raise _Interrumpir

    with pytest.raises(_Interrumpir):
        ij.correr_iteraciones("N3", modelos=["chico"], corrida=corrida, al_avanzar=_cortar)
    assert [it.numero for it in corrida.iteraciones] == [1, 2, 3]
    assert corrida.estado == ij.ESTADO_EN_CURSO and not corrida.completa


def test_cli_escribe_el_log_despues_de_cada_intento(parchear, tmp_path, capsys):
    parchear(ModeloIteraciones())
    escritos = []
    original = ij.a_json

    def _espiar(corrida):
        escritos.append(len(corrida.iteraciones))
        return original(corrida)

    ij.a_json, guardado = _espiar, ij.a_json
    try:
        assert ij.main(["N3", "--iteraciones", "3", "--pequeno", "chico", "--grande", "-",
                        "--out", str(tmp_path)]) == 0
    finally:
        ij.a_json = guardado
    assert escritos[:3] == [1, 2, 3], "el JSON se reescribe tras cada intento, no solo al final"
    doc = json.loads((tmp_path / "N3_iteraciones_juez.json").read_text(encoding="utf-8"))
    assert doc["estado"] == "completa" and doc["iteraciones_registradas"] == doc["iteraciones_esperadas"] == 3
    texto = (tmp_path / "N3_iteraciones_juez.txt").read_text(encoding="utf-8")
    assert "INTENTO 3 DE 3 · CHICO" in texto
    salida = capsys.readouterr().out
    assert "Intento 3 de 3 · chico" in salida


def test_cli_todas_corre_las_10(parchear, tmp_path):
    parchear(ModeloIteraciones())
    assert ij.main(["todas", "--iteraciones", "1", "--pequeno", "chico", "--grande", "-",
                    "--out", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*_iteraciones_juez.json"))) == len(ij.PREGUNTAS) == 10


# ---------------------------------------------------------------------------
# Trazabilidad: de qué intento sale cada cifra
# ---------------------------------------------------------------------------

def test_intento_guarda_resultado_crudo_y_redaccion_descartada(parchear):
    """Caso «¿de dónde salió el 121?»: el modelo escribe una cifra que no
    está en el resultado dos veces -> plantilla. Antes solo quedaba la
    respuesta final; ahora quedan las dos redacciones y el resultado."""
    parchear(ModeloIteraciones(redaccion=["Hay 141 pedidos."]))
    it = ij.correr_iteraciones("N3", modelos=["chico"], n=1).iteraciones[0]

    assert it.columnas == ["n"] and it.n_filas == 1 and it.filas == [[21]]
    assert it.primera_redaccion == "Hay 141 pedidos." and it.cifras_rechazadas == ["141"]
    assert it.segunda_redaccion == "Hay 141 pedidos."
    assert it.origen_respuesta == "plantilla" and "21" in it.respuesta and "141" not in it.respuesta
    texto = ij.a_texto(ij.correr_iteraciones("N3", modelos=["chico"], n=1))
    assert "Primera redacción del modelo: Hay 141 pedidos." in texto
    assert "Cifras de la primera redacción que NO están en el resultado: 141" in texto
    assert "Respuesta final [origen: plantilla]" in texto


def test_intento_guarda_el_sql_rechazado_y_su_motivo(parchear):
    parchear(ModeloIteraciones(sql=["SELECT columna_que_no_existe FROM orders", SQL_OK]))
    it = ij.correr_iteraciones("N3", modelos=["chico"], n=1).iteraciones[0]
    assert it.ok and it.intentos_sql == 2
    assert it.sql_rechazado == "SELECT columna_que_no_existe FROM orders"
    assert it.motivo_reintento and it.sql.startswith(SQL_OK)


def test_export_de_todas_las_corridas(parchear):
    parchear(ModeloIteraciones())
    corridas = [ij.correr_iteraciones(nid, modelos=["chico"], n=2) for nid in ("N1", "N3")]
    texto = ij.exportar_texto(corridas)
    assert "NECESIDAD N1" in texto and "NECESIDAD N3" in texto
    assert "RESPUESTA DE REFERENCIA (correcta" in texto and corridas[1].referencia in texto
    doc = json.loads(ij.exportar_json(corridas))
    assert [c["necesidad"] for c in doc["corridas"]] == ["N1", "N3"]
    assert doc["corridas"][0]["iteraciones"][1]["etiqueta"] == "Intento 2 de 2 · chico"


# ---------------------------------------------------------------------------
# Modo autocorrección encadenada
# ---------------------------------------------------------------------------

def test_autocorreccion_pasa_el_intento_anterior_al_siguiente(parchear):
    modelo = parchear(ModeloIteraciones(
        sql=["SELECT COUNT(*) AS n FROM orders WHERE order_dow = 0", SQL_OK],
        juez=[_juez("incorrecta", "la referencia cuenta todos los pedidos, no solo el día 0",
                    "Filtras por order_dow y la pregunta no pide filtrar por día."),
              _juez("correcta", "coincide", "")],
    ))
    # Fase 24: el juez como árbitro (en la Fase 25 sigue disponible con arbitro="juez").
    corrida = ij.correr_iteraciones("N3", modelos=["chico", "grande"], n=2, modo=ij.MODO_AUTOCORRECCION,
                                    arbitro=ij.ARBITRO_JUEZ)
    chico1, chico2, grande1, _ = corrida.iteraciones

    assert chico1.retroalimentacion is None
    retro = chico2.retroalimentacion
    assert "intento 1 de 2" in retro
    assert "SQL que generaste: SELECT COUNT(*) AS n FROM orders WHERE order_dow = 0" in retro
    # Fase 24: la forma del resultado, no sus filas; ni la redacción anterior.
    assert "devolvió 1 fila(s) con las columnas: n." in retro
    assert chico1.respuesta not in retro and "Respuesta que redactaste" not in retro
    assert "Veredicto del evaluador: INCORRECTA." in retro
    assert ("Qué está mal en el enfoque, según el evaluador: Filtras por order_dow y la pregunta no pide "
            "filtrar por día.") in retro
    # La comparación con la referencia (justificación) NUNCA pasa al siguiente intento.
    assert chico1.justificacion == "la referencia cuenta todos los pedidos, no solo el día 0"
    assert chico1.justificacion not in retro
    # Cada modelo tiene su propia cadena: el grande empieza de cero.
    assert grande1.retroalimentacion is None

    sql_prompts = [ll["usuario"] for ll in modelo.de_tipo("sql")]
    redaccion_prompts = [ll["usuario"] for ll in modelo.de_tipo("redaccion")]
    assert retro not in sql_prompts[0] and retro in sql_prompts[1]
    # Primera redacción de cada intento (el reintento estricto de cifras no lleva contexto).
    primeras = [u for u in redaccion_prompts if "Tu respuesta anterior tenía cifras" not in u]
    assert retro not in primeras[0] and retro in primeras[1]
    assert retro not in sql_prompts[2], "el intento 1 del grande no recibe la cadena del chico"


def test_autocorreccion_incluye_el_error_si_el_intento_fallo(parchear):
    parchear(ModeloIteraciones(sql=["DELETE FROM orders", SQL_OK]))
    corrida = ij.correr_iteraciones("N3", modelos=["chico"], n=2, modo=ij.MODO_AUTOCORRECCION,
                                    arbitro=ij.ARBITRO_JUEZ)
    primero, segundo = corrida.iteraciones
    assert not primero.ok
    assert f"Error (mensaje exacto): {primero.error}" in segundo.retroalimentacion
    assert "Veredicto del evaluador: INCORRECTA" in segundo.retroalimentacion


def test_modo_independiente_no_pasa_contexto(parchear):
    modelo = parchear(ModeloIteraciones())
    corrida = ij.correr_iteraciones("N3", modelos=["chico"], n=3, modo=ij.MODO_INDEPENDIENTE)
    assert all(it.retroalimentacion is None for it in corrida.iteraciones)
    assert len({ll["usuario"] for ll in modelo.de_tipo("sql")}) == 1, "los 3 prompts de SQL son idénticos"


def test_sin_retroalimentacion_los_prompts_no_cambian():
    assert pi.prompt_sql_instacart("p", "esq") == pi.prompt_sql_instacart("p", "esq", None, None)
    assert pi.prompt_sql_instacart("p", "esq")[1] == "p"
    assert pi.prompt_respuesta_instacart("p", "t") == pi.prompt_respuesta_instacart("p", "t", None)


def test_modo_desconocido_se_rechaza():
    with pytest.raises(ValueError):
        ij.nueva_corrida("N3", modelos=["chico"], n=1, modo="otro")


# ---------------------------------------------------------------------------
# UI: panel por intento
# ---------------------------------------------------------------------------

def _titulos_intentos(at) -> list[str]:
    return [m.value for m in at.markdown if m.value.startswith("##### ")]


def test_panel_muestra_todos_los_intentos_incluido_el_ultimo(app):
    at = app(ModeloIteraciones(juez=[_juez("incorrecta", "no"), _juez("incorrecta", "no"), _juez("correcta", "sí")]))
    at.number_input(key=app_instacart.CLAVE_ITERACIONES).set_value(3).run()
    at = _a_dashboard(at)
    at.button(key="iterar_N3").click().run()
    assert not list(at.exception), list(at.exception)

    titulos = _titulos_intentos(at)
    assert len(titulos) == 6
    for modelo in ("qwen2.5-coder:1.5b", "qwen2.5-coder:14b"):
        for k in (1, 2, 3):
            assert sum(f"Intento {k} de 3 · `{modelo}`" in t for t in titulos) == 1
    textos = "\n".join(m.value for m in at.markdown)
    assert "**Respuesta de referencia (correcta)**" in textos
    assert textos.count("**Resultado crudo de la consulta** (intento 3 de 3") == 2
    assert not any("Corrida interrumpida" in w.value for w in at.warning)
    labels = [b.proto.label for b in at.get("download_button")]
    assert "⬇️ N3_iteraciones_juez.txt (log legible)" in labels and f"⬇️ {ij.NOMBRE_EXPORT_TODAS_TEXTO}" in labels


def test_panel_avisa_corrida_interrumpida_y_muestra_lo_registrado(app, parchear):
    at = _a_dashboard(app(ModeloIteraciones()))
    corrida = ij.nueva_corrida("N3", modelos=["qwen2.5-coder:1.5b"], n=4)

    def _cortar(it, hechas, total):
        if hechas == 2:
            raise _Interrumpir

    with pytest.raises(_Interrumpir):
        ij.correr_iteraciones("N3", modelos=corrida.modelos, corrida=corrida, al_avanzar=_cortar)
    at.session_state[app_instacart.CLAVE_CORRIDAS] = {"N3": corrida}
    at.run()
    assert any("Corrida interrumpida: se registraron 2 de 4 intentos" in w.value for w in at.warning)
    assert len(_titulos_intentos(at)) == 2


def test_modo_por_defecto_es_autocorreccion_y_se_puede_cambiar(app):
    at = app(ModeloIteraciones())
    radio = at.selectbox(key=app_instacart.CLAVE_MODO_ITERACION)
    assert radio.value == ij.MODO_AUTOCORRECCION
    radio.set_value(ij.MODO_INDEPENDIENTE).run()
    at.number_input(key=app_instacart.CLAVE_ITERACIONES).set_value(2).run()
    at = _a_dashboard(at)
    at.button(key="iterar_N3").click().run()
    corrida = at.session_state[app_instacart.CLAVE_CORRIDAS]["N3"]
    assert corrida.modo == ij.MODO_INDEPENDIENTE
    assert all(it.retroalimentacion is None for it in corrida.iteraciones)
