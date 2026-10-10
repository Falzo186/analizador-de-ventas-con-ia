"""Fase 29: límite de ejecución propio de N9 (25 s) y pista de lentitud que
nombra el JOIN que multiplica filas. Sin Ollama ni la BD real."""
from __future__ import annotations

import duckdb
import pytest

from src.analytics.necesidades import DEFINICIONES
from src.llm import bitacora_chat as bc
from src.llm import formato_entrega as fe
from src.llm import iteraciones_juez as ij
from src.llm import responder_instacart as ri
from src.llm import retroalimentacion as rf
from src.llm.safety_instacart import TIMEOUT_S_DEFAULT, TimeoutConsulta, ejecutar_con_guardas


def test_solo_n9_tiene_limite_propio():
    assert DEFINICIONES["N9"].timeout_consulta_s == 25.0
    assert ij.timeout_consulta("N9") == 25.0
    for nid in DEFINICIONES:
        if nid != "N9":
            assert DEFINICIONES[nid].timeout_consulta_s is None, nid
            assert ij.timeout_consulta(nid) == TIMEOUT_S_DEFAULT == 10.0


@pytest.mark.parametrize("nid, esperado", [("N9", 25.0), ("N5", 10.0)])
def test_el_panel_de_iteraciones_pasa_el_limite_de_la_necesidad(monkeypatch, nid, esperado):
    vistos = {}

    def pipeline_falso(pregunta, **kwargs):
        vistos.update(kwargs)
        return ri.ResultadoInstacart(pregunta=pregunta, error="sin modelo")

    monkeypatch.setattr(ri, "responder_pregunta_instacart", pipeline_falso)
    ij.correr_iteracion(nid, "ref", "modelo-falso", 1, arbitro=ij.ARBITRO_CHEQUEO, medir_juez=False,
                        referencia_filas=[{"x": 1}])
    assert vistos["timeout_consulta_s"] == esperado


def test_la_entrega_de_ia_pasa_el_limite_de_la_necesidad(monkeypatch):
    vistos = {}

    def pipeline_falso(pregunta, **kwargs):
        vistos.update(kwargs)
        return ri.ResultadoInstacart(pregunta=pregunta, error="sin modelo")

    monkeypatch.setattr(ri, "responder_pregunta_instacart", pipeline_falso)
    nid = fe.NECESIDADES_ENTREGA[0]
    fe.generar_entrega_ia(nid, modelo="modelo-falso")
    assert vistos["timeout_consulta_s"] == ij.timeout_consulta(nid)


def test_el_pipeline_lleva_el_limite_hasta_la_guarda(monkeypatch):
    limites = []

    def guarda_falsa(con, sql, *, limite_filas, timeout_s):
        limites.append(timeout_s)
        return ["n"], [(1,)]

    monkeypatch.setattr(ri, "abrir_conexion_solo_lectura", lambda _: duckdb.connect())
    monkeypatch.setattr(ri, "construir_descripcion_esquema", lambda con: "esquema")
    monkeypatch.setattr(ri, "generar_sql_instacart", lambda *a, **k: "SELECT 1 AS n")
    monkeypatch.setattr(ri, "ejecutar_con_guardas", guarda_falsa)
    assert ri.responder_pregunta_instacart("p", redactar=False, timeout_consulta_s=25.0).ok
    assert ri.responder_pregunta_instacart("p", redactar=False).ok, "el chat no lo pasa"
    assert limites == [25.0, TIMEOUT_S_DEFAULT]


def test_la_guarda_real_corta_con_el_limite_pedido():
    con = duckdb.connect()
    lenta = "SELECT COUNT(*) FROM range(1000000000) a, range(1000) b WHERE a.range % 7 = b.range"
    with pytest.raises(TimeoutConsulta, match="límite de 1s"):
        ejecutar_con_guardas(con, lenta, timeout_s=1.0)


def test_pista_de_lentitud_nombra_el_join_que_multiplica_filas():
    assert "agrupa primero por los ids" in rf.PISTA_TIMEOUT
    assert len(rf.PISTA_TIMEOUT) <= bc.MAX_CARACTERES_LECCION, "cabe entera en una lección de la bitácora"
    assert rf.chequeo_error("La consulta excedió el límite de 25s y se interrumpió.") == [rf.PISTA_TIMEOUT]
    sin_memoria = "Out of Memory Error: could not allocate block of size 256.0 KiB (24.9 GiB/25.0 GiB used)"
    assert rf.chequeo_error(sin_memoria) == [rf.PISTA_TIMEOUT]
    assert bc.leccion_de_error(sin_memoria, "SELECT 1", {}).firma == "timeout"


def test_regla_de_pares_agrupa_por_ids_y_no_filtra_la_respuesta():
    from src.llm.esquema_instacart import REGLAS_DE_NEGOCIO
    from src.llm.safety_instacart import validar_sql

    assert "agrupa PRIMERO por los dos product_id" in REGLAS_DE_NEGOCIO
    i = REGLAS_DE_NEGOCIO.index("  SELECT pa.product_name")
    plantilla = " ".join(REGLAS_DE_NEGOCIO[i:].split())
    assert validar_sql(plantilla).startswith("SELECT"), "una sola sentencia que pasa la guarda del chat"
    sql_agregado = plantilla[plantilla.index("FROM (SELECT"):plantilla.index(") pares")]
    assert "GROUP BY a.product_id, b.product_id" in sql_agregado and "product_name" not in sql_agregado
    # Ninguna cifra ni nombre de la respuesta de referencia de N9.
    for fuga in ("64761", "64,761", "Banana", "Avocado", "10000"):
        assert fuga not in REGLAS_DE_NEGOCIO, fuga
