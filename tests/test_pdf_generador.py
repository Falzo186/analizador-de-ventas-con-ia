"""Tests del PDF determinista de Fase 4 (`src.pdf.generador`).

Todos corren contra la fixture chica (`tests/fixtures/instacart_needs/`),
no contra `data/instacart.duckdb` real: el texto de hallazgos/limitaciones
que estos tests verifican (censura, unidades = líneas, etc.) sale de
cadenas fijas en `src.analytics.necesidades`, no depende del volumen de
datos. Fase 24: 10 necesidades de una sola consulta. Mantenerlos fuera de `integration`
los deja correr siempre, sin depender de tener el dataset completo en disco.
"""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path

import pytest
from pypdf import PdfReader

from src.pdf import generador
from tests.helpers_analytics import construir_fixture

RAW_DIR = Path("data/instacart/raw")
NECESIDADES_MD = Path(__file__).resolve().parents[1] / "docs" / "NECESIDADES.md"

PATRON_DINERO = re.compile(r"ingres|dinero|ganancia|precio|revenue|\$|USD|MXN", re.IGNORECASE)
# Las únicas apariciones legítimas son disclaimers ("No hay precios...",
# "no se puede afirmar nada sobre ingresos o dinero...") -- el mismo criterio
# que ya usa tests/test_analytics_honestidad.py para `limitaciones` (ahí SÍ
# pueden mencionar dinero, precisamente para aclarar que no existe).
MARCADORES_NEGACION = (
    "no hay", "no se puede", "no dice nada", "no se afirma", "no tiene",
    "ni ganancia", "ni ingreso", "sin montos", "sin precios", "nunca en dinero",
)

# Limitaciones que el PDF debe mostrar sí o sí, verificadas por palabra
# clave (no texto exacto): son las que cambian cómo se lee la respuesta.
PALABRAS_CLAVE_CRITICAS = {
    "N1": ["100 pedidos por cliente"],
    "N2": ["no es el promedio de las tasas"],
    "N3": ["recompras / veces"],
    "N5": ["una unidad = una línea"],
    "N8": ["hora anónima"],
    "N9": ["co-ocurrencia por volumen"],
    "N10": ["100% nuevo por definición"],
    "N6": ["censurado en 30"],
    "N7": ["pasillo «missing»"],
}

# Literales numéricos permitidos fuera de configuración de layout (ver el
# bloque "Layout" al inicio de src/pdf/generador.py): coordenadas de tabla
# reportlab (0, 1, -1), estilo de gráfica (opacidad, bins, rotación, ancho
# de barra, fracciones de columna) y una tolerancia de comparación. Ninguno
# de estos representa una cifra de negocio -- esas SIEMPRE se leen de
# ResultadoNecesidad/Ficha, nunca aparecen como literal en este archivo.
LISTA_BLANCA_NUMERICA = {
    0, 1, -1, 2, 4, 10, 20, 45, 90, 1024,
    0.5, 0.55, 0.8, 0.7, 0.3, 0.2, 0.25, 0.18, 0.14, 0.68, 0.12, 0.22,
    1e-9, 150.0, 22, 16, 13,
}


@pytest.fixture(scope="module")
def pdf_generado(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("pdf_fixture")
    src = construir_fixture(tmp_path)
    out_pdf = tmp_path / "reporte.pdf"
    t0 = time.perf_counter()
    resumen = generador.generar_pdf(
        db_path=src, out_dir=tmp_path / "referencia",
        out_pdf=out_pdf, raw_dir=RAW_DIR,
    )
    duracion_medida = time.perf_counter() - t0
    return resumen, duracion_medida


@pytest.fixture(scope="module")
def texto_pdf(pdf_generado) -> str:
    resumen, _ = pdf_generado
    lector = PdfReader(str(resumen.ruta))
    return "\n".join(pagina.extract_text() for pagina in lector.pages)


# ---------------------------------------------------------------------------
# Estructura del documento
# ---------------------------------------------------------------------------

def test_pdf_se_genera_y_tiene_paginas(pdf_generado):
    resumen, _ = pdf_generado
    assert resumen.ruta.exists()
    lector = PdfReader(str(resumen.ruta))
    # Portada + Índice + 10 secciones + Apéndice (>=1 página) + Parámetros
    # (>=1 página): al menos 13 páginas incluso en la fixture chica.
    assert len(lector.pages) >= 13


def test_pdf_tiene_las_10_secciones_de_necesidad(texto_pdf):
    for nid in generador.NECESIDADES_ORDEN:
        assert re.search(rf"\b{nid}\s+—", texto_pdf), f"no se encontró el encabezado de {nid} en el PDF"


def test_pdf_tiene_indice_apendice_y_parametros(texto_pdf):
    assert "Índice" in texto_pdf
    assert "Apéndice: consultas SQL ejecutadas" in texto_pdf
    assert "Parámetros de cada necesidad" in texto_pdf


def test_apendice_incluye_sql_de_cada_necesidad(texto_pdf):
    # Un fragmento de SQL que solo aparece en las consultas reales (no en
    # prosa): confirma que el apéndice trae las consultas, no un resumen.
    assert "SELECT" in texto_pdf
    assert texto_pdf.count("SELECT") >= len(generador.NECESIDADES_ORDEN)  # al menos 1 por necesidad


def test_cada_seccion_trae_respuesta_y_resultado_crudo(texto_pdf):
    assert texto_pdf.count("Resultado crudo de la consulta") == len(generador.NECESIDADES_ORDEN)
    assert texto_pdf.count("Respuesta") >= len(generador.NECESIDADES_ORDEN)


# ---------------------------------------------------------------------------
# Limitaciones críticas (punto 2c)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nid", sorted(PALABRAS_CLAVE_CRITICAS))
def test_limitacion_critica_presente(texto_pdf, nid):
    palabras = PALABRAS_CLAVE_CRITICAS[nid]
    # El PDF parte las líneas: se compara con los espacios normalizados.
    normal = " ".join(texto_pdf.lower().split())
    encontrada = any(p.lower() in normal for p in palabras)
    assert encontrada, f"{nid}: ninguna de {palabras} aparece en el texto del PDF"


# ---------------------------------------------------------------------------
# Honestidad: sin lenguaje de dinero salvo en disclaimers de negación
# ---------------------------------------------------------------------------

def test_pdf_sin_lenguaje_de_dinero_fuera_de_disclaimers(texto_pdf):
    texto_plano = texto_pdf.replace("\n", " ")
    ofensores = []
    for m in PATRON_DINERO.finditer(texto_plano):
        ventana = texto_plano[max(0, m.start() - 60): m.end() + 60].lower()
        if not any(marcador in ventana for marcador in MARCADORES_NEGACION):
            ofensores.append(texto_plano[max(0, m.start() - 40): m.end() + 40])
    assert not ofensores, f"lenguaje de dinero fuera de un disclaimer de negación: {ofensores}"


# ---------------------------------------------------------------------------
# Ficha del dataset: el PDF usa construir_ficha(), no cifras propias
# ---------------------------------------------------------------------------

def test_portada_cifras_coinciden_con_ficha(tmp_path):
    src = construir_fixture(tmp_path)
    out_pdf = tmp_path / "reporte2.pdf"
    resumen = generador.generar_pdf(
        db_path=src, out_dir=tmp_path / "referencia",
        out_pdf=out_pdf, raw_dir=RAW_DIR,
    )
    import duckdb
    con = duckdb.connect(str(src), read_only=True)
    from scripts.generar_ficha_dataset import construir_ficha
    ficha = construir_ficha(con, src, RAW_DIR)
    con.close()

    texto = "\n".join(p.extract_text() for p in PdfReader(str(resumen.ruta)).pages)
    for d in ficha.dimensiones:
        assert f"{d.filas:,}" in texto, f"portada no muestra las {d.filas:,} filas de {d.nombre}"
    assert f"{ficha.n_pass} PASS" in texto
    assert f"{ficha.n_fail} FAIL" in texto


# ---------------------------------------------------------------------------
# Determinismo de contenido (no de bytes)
# ---------------------------------------------------------------------------

_PATRON_FECHA_PORTADA = re.compile(r"Generado el \S+")
_PATRON_FECHA_METADATO = re.compile(r'"fecha_generacion":\s*"[^"]*"')


def _texto_sin_fecha(texto: str) -> str:
    return _PATRON_FECHA_PORTADA.sub("Generado el [omitido]", texto)


def test_determinismo_de_contenido_dos_corridas(tmp_path):
    src = construir_fixture(tmp_path)
    out_dir = tmp_path / "referencia"

    pdf1 = tmp_path / "r1.pdf"
    r1 = generador.generar_pdf(db_path=src, out_dir=out_dir, out_pdf=pdf1, raw_dir=RAW_DIR)
    texto1 = "\n".join(p.extract_text() for p in PdfReader(str(r1.ruta)).pages)

    pdf2 = tmp_path / "r2.pdf"
    r2 = generador.generar_pdf(db_path=src, out_dir=out_dir, out_pdf=pdf2, raw_dir=RAW_DIR)
    texto2 = "\n".join(p.extract_text() for p in PdfReader(str(r2.ruta)).pages)

    assert _texto_sin_fecha(texto1) == _texto_sin_fecha(texto2), (
        "el texto extraído difiere entre dos corridas idénticas (descontando la fecha de generación)"
    )
    # Los bytes crudos SÍ pueden diferir (metadata interna de reportlab);
    # eso es justamente lo que este test no exige -- ver docstring de la
    # sección 3 del prompt ("determinismo de CONTENIDO, no de bytes").


# ---------------------------------------------------------------------------
# Tiempo de generación
# ---------------------------------------------------------------------------

def test_tiempo_de_generacion_bajo_2_minutos(pdf_generado):
    resumen, duracion_medida = pdf_generado
    assert resumen.duracion_s < 120
    assert duracion_medida < 120


# ---------------------------------------------------------------------------
# Cero cifras hardcodeadas (AST, lista blanca explícita)
# ---------------------------------------------------------------------------

def test_generador_sin_literales_numericos_sospechosos():
    ruta = Path(generador.__file__)
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))

    ids_en_constantes_nombradas = set()
    for nodo in ast.walk(arbol):
        es_asignacion_top_level = (
            isinstance(nodo, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id.isupper() for t in nodo.targets)
        )
        if es_asignacion_top_level:
            for sub in ast.walk(nodo.value):
                if isinstance(sub, ast.Constant):
                    ids_en_constantes_nombradas.add(id(sub))

    sospechosos = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Constant):
            continue
        if not isinstance(nodo.value, (int, float)) or isinstance(nodo.value, bool):
            continue
        if id(nodo) in ids_en_constantes_nombradas:
            continue
        if nodo.value in LISTA_BLANCA_NUMERICA:
            continue
        sospechosos.append((nodo.lineno, nodo.value))

    assert not sospechosos, (
        f"literales numéricos fuera de la lista blanca de layout: {sospechosos} -- "
        "si es una cifra de negocio, debe salir de ResultadoNecesidad/Ficha; si es "
        "de layout/estilo, agrégala a LISTA_BLANCA_NUMERICA en este test o nómbrala "
        "como constante ALLCAPS en src/pdf/generador.py"
    )


def test_pdf_deja_de_estar_vigente_si_cambia_el_contenido_de_una_necesidad(tmp_path):
    """Fase 28: al reemplazar una pregunta la huella de la BD no cambia; el PDF
    tiene que darse por desactualizado igual (compara la huella de contenido)."""
    import json

    src = construir_fixture(tmp_path)
    out_dir, pdf = tmp_path / "referencia", tmp_path / "r.pdf"
    generador.generar_pdf(db_path=src, out_dir=out_dir, out_pdf=pdf, raw_dir=RAW_DIR)
    assert generador.pdf_vigente(pdf, db_path=src, out_dir=out_dir)

    ruta = out_dir / "N2.json"  # misma huella de BD, mismo SQL y pregunta: solo cambia el contenido
    data = json.loads(ruta.read_text(encoding="utf-8"))
    data["hallazgos"] = ["otra redacción"]
    ruta.write_text(json.dumps(data), encoding="utf-8")
    assert not generador.pdf_vigente(pdf, db_path=src, out_dir=out_dir)
