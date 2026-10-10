"""Fase 24: retroalimentación específica entre intentos SIN fugar la respuesta
(`src.llm.retroalimentacion`, `src.llm.iteraciones_juez.texto_retroalimentacion`).

El test central (`test_retroalimentacion_no_contiene_valores_de_la_referencia`)
corre, contra la BD real y las referencias reales del Paso 1
(`data/referencia/Nx.json`), el PEOR caso para cada una de las 10 necesidades:

- el intento 1 ejecuta exactamente el SQL de referencia (su resultado ES la
  respuesta correcta),
- su redacción cita la respuesta,
- el juez cita la respuesta en la "comparacion" y en una de las dos oraciones
  del "diagnostico_enfoque",

y verifica que el texto que recibe el intento 2 no contiene NINGÚN valor de la
referencia. La verificación NO usa `retroalimentacion.fugas` (sería probar la
función consigo misma): genera por su cuenta los formatos en que un modelo
escribiría cada valor y los busca como token.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.analytics import necesidades as nec
from src.dashboard import servicio
from src.llm import iteraciones_juez as ij
from src.llm import responder_instacart as ri
from src.llm import retroalimentacion as rf
from tests.test_fase11_iteraciones import ModeloIteraciones, _juez, parchear, rutas  # noqa: F401

DB_REAL = Path("data/instacart.duckdb")
DIAGNOSTICO_LIMPIO = "Ordena de mayor a menor por la métrica que pide la pregunta."


# ---------------------------------------------------------------------------
# Detector independiente de valores de la referencia
# ---------------------------------------------------------------------------

def _formatos(v: object) -> set[str]:
    """Cómo escribiría un modelo (o un juez) el valor `v`."""
    if isinstance(v, str):
        return {v}
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return set()
    out = set()
    if float(v).is_integer():
        n = int(v)
        out |= {str(n), f"{n:,}", f"{n:,}".replace(",", "."), f"{n:,}".replace(",", " ")}
    else:
        for x in (v, v * 100):
            out |= {f"{x:.1f}", f"{x:.2f}", f"{x:.3f}", f"{x:.4f}"}
            out |= {s.replace(".", ",") for s in list(out)}
            if round(x) >= rf.MIN_ENTERO_PROTEGIDO:
                out.add(str(round(x)))
    return out


def _numeros_de(texto: str) -> set[str]:
    return {m.group(0) for m in re.finditer(r"\d+(?:[.,  ]\d+)*", texto)}


def valores_fugados(texto: str, pregunta: str, filas_referencia: list[dict]) -> list[str]:
    """Valores de la referencia presentes en `texto`. Se excluyen, igual que en
    el módulo (decisión documentada): los números de la pregunta (10, 50, 1) y
    los enteros menores que 10 (0, 1, 2 recompras: demasiado genéricos)."""
    de_la_pregunta = _numeros_de(pregunta)
    fugados = []
    for fila in filas_referencia:
        for v in fila.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer() \
                    and abs(v) < rf.MIN_ENTERO_PROTEGIDO:
                continue
            for f in _formatos(v):
                if f in de_la_pregunta:
                    continue
                patron = (rf"(?<![\w]){re.escape(f.casefold())}(?![\w])" if isinstance(v, str)
                          else rf"(?<![\d.,]){re.escape(f)}(?![\d]|[.,]\d)")
                if re.search(patron, texto.casefold() if isinstance(v, str) else texto):
                    fugados.append(f)
    return fugados


# ---------------------------------------------------------------------------
# Test central: contra la BD real y las referencias del Paso 1
# ---------------------------------------------------------------------------

@pytest.fixture()
def bd_real(monkeypatch):
    if not DB_REAL.exists():
        pytest.skip(f"no existe {DB_REAL}")
    monkeypatch.setattr(servicio, "DB_PATH_DEFAULT", DB_REAL)
    monkeypatch.setattr(ri, "DB_PATH_DEFAULT", DB_REAL)


def _cita(filas: list[dict], d: nec.DefNecesidad) -> str:
    """Una frase que cita la respuesta de la referencia en varios formatos."""
    f0 = filas[0]
    partes = [str(f0[d.columna_nombre])] if d.columna_nombre else []
    v = f0[d.columna_valor]
    partes += [f"{v:,}" if isinstance(v, int) else f"{v:.2f}", f"{v * 100:.1f}%" if isinstance(v, float) else ""]
    if len(filas) > 1 and d.columna_nombre:
        partes.append(str(filas[1][d.columna_nombre]))
    return " y ".join(p for p in partes if p)


#: Escenarios del peor caso (Fase 25):
#: - "juez": el juez-LLM es el árbitro (Fase 24) y su diagnóstico cita la respuesta.
#: - "chequeo_falla": el chequeo automático es el árbitro y se FUERZA que diga que falló (aunque el
#:   resultado sea el de la referencia), para que se llame al diagnóstico y este cite la respuesta.
#: - "chequeo_acierta": el chequeo dice que acertó: no hay diagnóstico, solo "conserva el enfoque".
ESCENARIOS = ("juez", "chequeo_falla", "chequeo_acierta")


@pytest.mark.integration
@pytest.mark.parametrize("escenario", ESCENARIOS)
@pytest.mark.parametrize("nid", list(nec.IDS))
def test_retroalimentacion_no_contiene_valores_de_la_referencia(bd_real, monkeypatch, nid, escenario):
    import json as _json

    d = nec.DEFINICIONES[nid]
    r = servicio.cargar_necesidad(nid).resultado
    filas_ref = r.tablas[nec.CONSULTA]
    cita = _cita(filas_ref, d)
    modelo = ModeloIteraciones(
        sql=[d.sql()],
        redaccion=[f"La respuesta es {cita}."],
        juez=[_juez("correcta", f"Coincide con la referencia: {cita}.",
                    f"{DIAGNOSTICO_LIMPIO} La respuesta correcta es {cita}.")],
        diagnostico=[_json.dumps({"comparacion": f"Debió dar {cita}.",
                                  "diagnostico_enfoque": f"{DIAGNOSTICO_LIMPIO} La respuesta correcta es {cita}."},
                                 ensure_ascii=False)],
    )
    from src.llm import client
    monkeypatch.setattr(client, "chat_con_modelo", modelo.chat_con_modelo)
    original = rf.coincide_con_referencia
    if escenario == "chequeo_falla":
        monkeypatch.setattr(rf, "coincide_con_referencia",
                            lambda *a, **k: (False, f"forzado en el test; el real: {original(*a, **k)}"))
    arbitro = ij.ARBITRO_JUEZ if escenario == "juez" else ij.ARBITRO_CHEQUEO

    corrida = ij.correr_iteraciones(nid, modelos=["simulado"], n=2, modo=ij.MODO_AUTOCORRECCION, arbitro=arbitro)
    primero, segundo = corrida.iteraciones
    retro = segundo.retroalimentacion

    # El peor caso es real: el intento 1 SÍ tenía la respuesta, y el juez la citó.
    assert primero.ok and original(nid, corrida.referencia_filas, primero.filas)[0]
    assert valores_fugados(primero.juez_justificacion, d.pregunta, filas_ref), "la opinión del juez sí la cita"
    # Lo que pasa al intento 2 no tiene ningún valor de la referencia...
    assert valores_fugados(retro, d.pregunta, filas_ref) == [], retro
    assert rf.fugas(retro, corrida.valores_protegidos) == [], retro
    # ...pero sigue siendo específico.
    assert f"SQL que generaste: {' '.join(d.sql().split())}" in retro
    assert "devolvió" in retro and "columnas:" in retro
    if escenario == "chequeo_acierta":
        assert "Veredicto (chequeo automático contra la respuesta correcta): CORRECTA." in retro
        assert "conserva el enfoque" in retro and "enfoque, según" not in retro
        assert primero.diagnostico is None and not modelo.de_tipo("diagnostico")
    else:
        assert valores_fugados(primero.diagnostico, d.pregunta, filas_ref), "el diagnóstico crudo sí la cita"
        esperado = ("Veredicto del evaluador: CORRECTA." if escenario == "juez"
                    else "Veredicto (chequeo automático contra la respuesta correcta): INCORRECTA.")
        assert esperado in retro
        assert f"Qué está mal en el enfoque, según el evaluador: {DIAGNOSTICO_LIMPIO}" in retro
        assert primero.oraciones_omitidas == 1
    # Y eso es exactamente lo que llegó al modelo en el prompt de SQL del intento 2.
    assert retro in modelo.de_tipo("sql")[1]["usuario"]
    assert valores_fugados(modelo.de_tipo("sql")[1]["usuario"].split(retro)[0], d.pregunta, filas_ref) == []


# ---------------------------------------------------------------------------
# Error de ejecución: el mensaje de DuckDB pasa completo
# ---------------------------------------------------------------------------

def test_error_de_duckdb_pasa_completo_con_candidate_bindings(parchear):
    parchear(ModeloIteraciones(sql=["SELECT department, COUNT(*) AS n FROM order_products GROUP BY department"]))
    corrida = ij.correr_iteraciones("N2", modelos=["chico"], n=2, modo=ij.MODO_AUTOCORRECCION)
    primero, segundo = corrida.iteraciones
    assert not primero.ok and "Candidate bindings" in primero.error
    assert f"Error (mensaje exacto): {primero.error}" in segundo.retroalimentacion
    assert f"Motivo del rechazo (mensaje exacto): {primero.motivo_reintento}" in segundo.retroalimentacion
    assert "Candidate bindings" in segundo.retroalimentacion and "LINE 1:" in segundo.retroalimentacion


def test_timeout_agrega_una_pista_sin_referencia():
    assert rf.chequeo_error("La consulta tardó demasiado y se canceló: La consulta excedió el límite de 10s") \
        == [rf.PISTA_TIMEOUT]
    assert rf.chequeo_error("Binder Error: ...") == []


# ---------------------------------------------------------------------------
# Unidades: valores protegidos, fugas y saneo
# ---------------------------------------------------------------------------

FILAS_N3 = [{"product_name": "Raw Veggie Wrappers", "veces_comprado": 69, "recompras": 65,
             "tasa_recompra": 0.9420289855072463},
            {"product_name": "Gel Food Colors", "veces_comprado": 50, "recompras": 0, "tasa_recompra": 0.0}]
PREGUNTA_N3 = nec.DEFINICIONES["N3"].pregunta


@pytest.mark.parametrize("texto", [
    "El primero es raw veggie wrappers.", "tiene 65 recompras", "una tasa de 94.2%", "tasa 0.94",
    "alrededor de 94 por ciento", "0,942", "con 69 compras",
])
def test_fugas_detecta_cada_formato(texto):
    assert rf.fugas(texto, rf.valores_protegidos(PREGUNTA_N3, FILAS_N3)), texto


@pytest.mark.parametrize("texto", [
    "Usa HAVING COUNT(*) >= 50.",          # 50 está en la pregunta
    "Devuelve 10 filas.",                  # 10 no es un valor de la referencia de N3
    "Agrupa por product_name y divide recompras entre veces comprado.",
    "reordered = 0 cuenta como primera compra.",  # enteros < 10 no se protegen
])
def test_fugas_no_marca_lo_que_no_es_la_respuesta(texto):
    assert rf.fugas(texto, rf.valores_protegidos(PREGUNTA_N3, FILAS_N3)) == [], texto


def test_sanear_quita_solo_las_oraciones_que_citan_la_referencia():
    vp = rf.valores_protegidos(PREGUNTA_N3, FILAS_N3)
    texto, quitadas = rf.sanear("Te falta dividir entre el total. El correcto es Raw Veggie Wrappers con 94.2%. "
                                "Falta el HAVING.", vp)
    assert (texto, quitadas) == ("Te falta dividir entre el total. Falta el HAVING.", 1)


def test_sanear_todo_citado_deja_el_aviso_sin_valores():
    vp = rf.valores_protegidos(PREGUNTA_N3, FILAS_N3)
    texto, quitadas = rf.sanear("Debió salir Raw Veggie Wrappers. Su tasa es 0.94.", vp)
    assert (texto, quitadas) == (rf.DIAGNOSTICO_OMITIDO, 2)
    assert rf.fugas(rf.DIAGNOSTICO_OMITIDO, vp) == []


# ---------------------------------------------------------------------------
# Chequeo de forma (sin la referencia) y coincidencia automática
# ---------------------------------------------------------------------------

def test_chequeo_forma_pistas():
    assert rf.chequeo_forma("N1", ["product_name", "n"], [("A", 3), ("B", 2)], 2) == [
        "La pregunta pide 10 filas (un top 10) y tu consulta devolvió 2."]
    # N3 no fija cuántas filas: no hay pista de cantidad.
    assert rf.chequeo_forma("N3", ["product_name", "t"], [("A", 0.9), ("B", 0.8)], 2) == []
    assert rf.chequeo_forma("N5", ["avg"], [(1.0,), (2.0,)], 2) == [
        "La pregunta pide un solo número (una fila) y tu consulta devolvió 2 filas."]
    sin_nombre = rf.chequeo_forma("N2", ["department_id", "n"], [(4, 9), (16, 5)], 2)
    assert any("ninguna columna de texto" in p and "departamento" in p for p in sin_nombre)
    sin_orden = rf.chequeo_forma("N2", ["department", "n"], [("a", 5), ("b", 9), ("c", 1)], 3)
    assert any("sin ordenar" in p for p in sin_orden)
    assert any("1,000 o más" in p for p in rf.chequeo_forma("N1", ["p", "n"], [("a", 1)] * 1000, 1000))


def test_coincidencia_automatica():
    ref_n2 = [{"department": "produce", "pedidos": 100}, {"department": "dairy eggs", "pedidos": 90}]
    assert rf.coincide_con_referencia("N2", ref_n2, [("produce", 100)])[0]
    assert not rf.coincide_con_referencia("N2", ref_n2, [("produce", 400)])[0], "nombre bien, métrica mal"
    assert rf.coincide_con_referencia("N2", ref_n2, [("produce",)])[0], "solo el nombre: es lo que pide la pregunta"
    assert not rf.coincide_con_referencia("N2", ref_n2, [("dairy eggs",)])[0]
    ref_n10 = [{"pct_lineas_recompradas": 59.0062, "lineas_recompradas": 1, "lineas_totales": 2}]
    assert rf.coincide_con_referencia("N10", ref_n10, [(0.590062,)])[0], "fracción vs. porcentaje"
    assert not rf.coincide_con_referencia("N10", ref_n10, [(41.0,)])[0]
    ref_n4 = [{"product_name": f"p{i}", "tasa_recompra": 0.0} for i in range(10)]
    assert rf.coincide_con_referencia("N4", ref_n4, [(f"p{i}", 0.0) for i in (3, 1, 7)])[0], "empates en otro orden"
    assert not rf.coincide_con_referencia("N4", ref_n4, [("x", 0.0), ("y", 0.0), ("p1", 0.0)])[0]
    assert not rf.coincide_con_referencia("N1", [{"product_name": "a", "unidades": 1}], None)[0]
