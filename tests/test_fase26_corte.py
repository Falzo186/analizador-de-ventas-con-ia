"""Fase 26: en autocorrección, la cadena de cada modelo se corta en el primer
acierto; los intentos restantes quedan "no ejecutados (ya convergió)". No
aplica al modo independiente (mide variabilidad). Mismo modelo simulado y
fixture chica que la Fase 11; en la fixture, el SQL de referencia de N5
acierta y `SQL_OK` falla.
"""

from __future__ import annotations

import json

import pytest

from src.analytics import necesidades as nec
from src.llm import iteraciones_juez as ij
from src.ui import app_instacart
from tests.test_fase11_iteraciones import (  # noqa: F401 - fixtures
    SQL_OK,
    ModeloIteraciones,
    _a_dashboard,
    _juez,
    _metricas_iteraciones,
    app,
    parchear,
    rutas,
)

SQL_N5 = nec.DEFINICIONES["N5"].sql()


def test_corta_en_el_primer_acierto_y_marca_el_resto_como_no_ejecutado(parchear):
    modelo = parchear(ModeloIteraciones(sql=[SQL_OK, SQL_N5, SQL_OK]))
    hechas = []
    corrida = ij.correr_iteraciones("N5", modelos=["chico"], n=5,
                                    al_avanzar=lambda it, h, t: hechas.append((it.numero, h, t)))

    assert corrida.corta_al_acertar and corrida.modo == ij.MODO_AUTOCORRECCION
    assert [(it.numero, it.veredicto) for it in corrida.iteraciones] == [(1, ij.INCORRECTA), (2, ij.CORRECTA)]
    assert len(modelo.de_tipo("sql")) == 2, "los intentos 3-5 no llaman al modelo"
    assert corrida.convergencia == {"chico": 2} and corrida.no_ejecutadas("chico") == [3, 4, 5]
    # No es una corrida interrumpida: los 5 intentos están contados (2 ejecutados + 3 no ejecutados).
    assert corrida.completa and corrida.estado == ij.ESTADO_COMPLETA
    assert hechas[-1] == (2, 5, 5), "el progreso llega al total"
    r = corrida.resumen()["chico"]
    assert (r.n, r.correctas, r.incorrectas, r.no_ejecutadas, r.estado_final) == (2, 1, 1, 3, ij.CORRECTA)


def test_lo_no_ejecutado_no_cuenta_como_fallido_ni_vacio_en_log_y_json(parchear):
    parchear(ModeloIteraciones(sql=[SQL_OK, SQL_N5]))
    corrida = ij.correr_iteraciones("N5", modelos=["chico"], n=4)
    doc = json.loads(ij.a_json(corrida))
    assert doc["corta_al_acertar"] is True and doc["convergencia"] == {"chico": 2}
    assert doc["iteraciones_registradas"] == 2 and doc["iteraciones_esperadas"] == 4
    assert doc["no_ejecutadas"] == [
        {"modelo": "chico", "numero": k, "etiqueta": f"Intento {k} de 4 · chico",
         "estado": "no ejecutada (ya convergió en el intento 2)"} for k in (3, 4)
    ]
    assert all(it["numero"] in (1, 2) for it in doc["iteraciones"])
    texto = ij.a_texto(corrida)
    assert "2 de 4 intentos ejecutados, 2 no ejecutados porque ya había convergido" in texto
    assert "INTENTO 3 DE 4 · CHICO  →  NO EJECUTADA (YA CONVERGIÓ EN EL INTENTO 2)" in texto
    assert "INTENTO 4 DE 4 · CHICO  →  NO EJECUTADA (YA CONVERGIÓ EN EL INTENTO 2)" in texto
    assert "termina en «correcta»" in texto


def test_sin_acierto_corre_todos_los_intentos(parchear):
    parchear(ModeloIteraciones(sql=[SQL_OK]))
    corrida = ij.correr_iteraciones("N5", modelos=["chico"], n=3)
    assert len(corrida.iteraciones) == 3 and not corrida.convergencia and corrida.completa
    assert corrida.resumen()["chico"].estado_final == ij.INCORRECTA


def test_cada_modelo_tiene_su_propio_corte(parchear):
    modelo = parchear(ModeloIteraciones(sql=[SQL_N5, SQL_OK, SQL_OK, SQL_N5]))
    corrida = ij.correr_iteraciones("N5", modelos=["chico", "grande"], n=3)
    # chico: 1✓ (corta). grande: 1✗ 2✗ 3✓.
    assert [(it.modelo, it.numero, it.veredicto) for it in corrida.iteraciones] == [
        ("chico", 1, ij.CORRECTA), ("grande", 1, ij.INCORRECTA), ("grande", 2, ij.INCORRECTA),
        ("grande", 3, ij.CORRECTA)]
    assert corrida.convergencia == {"chico": 1, "grande": 3}
    assert corrida.no_ejecutadas("chico") == [2, 3] and corrida.no_ejecutadas("grande") == []
    assert corrida.completa and len(modelo.de_tipo("sql")) == 4


def test_sin_corte_reproduce_la_fase_25(parchear):
    parchear(ModeloIteraciones(sql=[SQL_N5]))
    corrida = ij.correr_iteraciones("N5", modelos=["chico"], n=3, corta_al_acertar=False)
    assert len(corrida.iteraciones) == 3 and not corrida.convergencia


def test_modo_independiente_nunca_corta(parchear):
    """El modo independiente mide variabilidad entre N muestras: cortar en el
    primer acierto arruinaría esa medición, así que no aplica."""
    parchear(ModeloIteraciones(sql=[SQL_N5], juez=[_juez("correcta")]))
    corrida = ij.correr_iteraciones("N5", modelos=["chico"], n=3, modo=ij.MODO_INDEPENDIENTE)
    assert not corrida.corta_al_acertar
    assert [it.veredicto for it in corrida.iteraciones] == [ij.CORRECTA] * 3
    with pytest.raises(ValueError, match="solo aplica al modo autocorrección"):
        ij.nueva_corrida("N5", modelos=["chico"], n=3, modo=ij.MODO_INDEPENDIENTE, corta_al_acertar=True)


def test_con_arbitro_juez_corta_en_el_correcta_del_juez(parchear):
    parchear(ModeloIteraciones(sql=[SQL_N5], juez=[_juez("parcialmente correcta"), _juez("correcta")]))
    corrida = ij.correr_iteraciones("N5", modelos=["chico"], n=4, arbitro=ij.ARBITRO_JUEZ)
    assert [it.veredicto for it in corrida.iteraciones] == [ij.PARCIAL, ij.CORRECTA]
    assert corrida.convergencia == {"chico": 2}


def test_interrupcion_antes_de_converger_sigue_en_curso(parchear):
    class _Cortar(BaseException):
        pass

    parchear(ModeloIteraciones(sql=[SQL_OK]))
    corrida = ij.nueva_corrida("N5", modelos=["chico"], n=4)

    def _avance(it, hechas, total):
        if it.numero == 2:
            raise _Cortar

    with pytest.raises(_Cortar):
        ij.correr_iteraciones("N5", modelos=["chico"], corrida=corrida, al_avanzar=_avance)
    assert len(corrida.iteraciones) == 2 and not corrida.completa and corrida.estado == ij.ESTADO_EN_CURSO


def test_cli_corta_por_defecto_y_sin_corte_lo_desactiva(parchear, tmp_path, capsys):
    parchear(ModeloIteraciones(sql=[SQL_N5]))
    assert ij.main(["N5", "--iteraciones", "3", "--pequeno", "chico", "--grande", "-", "--out", str(tmp_path)]) == 0
    salida = capsys.readouterr().out
    assert "=== Intentos 2-3 de 3 · chico: no ejecutada (ya convergió en el intento 1) ===" in salida
    assert "1 de 3 intentos ejecutados, 2 no ejecutados porque ya había convergido" in salida
    assert ij.main(["N5", "--iteraciones", "3", "--pequeno", "chico", "--grande", "-", "--sin-corte",
                    "--out", str(tmp_path)]) == 0
    doc = json.loads((tmp_path / "N5_iteraciones_juez.json").read_text(encoding="utf-8"))
    assert doc["corta_al_acertar"] is False and len(doc["iteraciones"]) == 3


def test_panel_muestra_la_convergencia_y_los_intentos_no_ejecutados(app):
    at = app(ModeloIteraciones(sql=[SQL_OK, SQL_N5]))
    at.number_input(key=app_instacart.CLAVE_ITERACIONES).set_value(4).run()
    at = _a_dashboard(at, "N5")
    at.button(key="iterar_N5").click().run()
    assert not list(at.exception), list(at.exception)

    assert set(_metricas_iteraciones(at).values()) == {"✅ Convergió en el intento 2"}
    infos = [i.value for i in at.info]
    for modelo in ("qwen2.5-coder:1.5b", "qwen2.5-coder:14b"):
        for k in (3, 4):
            assert (f"⏭️ Intento {k} de 4 · `{modelo}` → no ejecutada (ya convergió en el intento 2): "
                    "no falló, no se corrió.") in infos
    captions = "\n".join(c.value for c in at.caption)
    assert "Secuencia: 1❌ → 2✅ → 3⏭️ → 4⏭️" in captions
    assert "(de 2 ejecutadas de 4; 2 no ejecutadas porque ya convergió)" in captions
    assert not any(w.value.startswith("Corrida interrumpida") for w in at.warning)
