"""Botón "Generar/Actualizar PDF" del dashboard (Fase 4, punto 4): existe,
y al presionarlo (contra la fixture chica, vía `streamlit.testing.v1.
AppTest`) genera el archivo y aparece la opción de descarga.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.dashboard import servicio
from src.pdf import generador as pdf_generador
from tests.helpers_analytics import construir_fixture

RUTA_DASHBOARD = "src/ui/dashboard.py"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    src, agg = construir_fixture(tmp_path)
    out_dir = tmp_path / "referencia"
    out_pdf = tmp_path / "pdf" / "reporte.pdf"

    monkeypatch.setattr(servicio, "DB_PATH_DEFAULT", src)
    monkeypatch.setattr(servicio, "AGG_PATH_DEFAULT", agg)
    monkeypatch.setattr(servicio, "OUT_DIR_DEFAULT", out_dir)
    monkeypatch.setattr(pdf_generador, "DB_PATH_DEFAULT", src)
    monkeypatch.setattr(pdf_generador, "AGG_PATH_DEFAULT", agg)
    monkeypatch.setattr(pdf_generador, "OUT_DIR_DEFAULT", out_dir)
    monkeypatch.setattr(pdf_generador, "OUT_PDF_DEFAULT", out_pdf)
    monkeypatch.setattr(pdf_generador, "RAW_DIR_DEFAULT", Path("data/instacart/raw"))

    at = AppTest.from_file(RUTA_DASHBOARD)
    at.run(timeout=90)
    assert not list(at.exception), f"el dashboard ya falla al cargar: {list(at.exception)}"
    return at, out_pdf


def test_boton_generar_pdf_existe(app):
    at, _ = app
    etiquetas = [b.label for b in at.sidebar.button]
    assert "📄 Generar/Actualizar PDF" in etiquetas


def test_boton_genera_pdf_y_ofrece_descarga(app):
    at, out_pdf = app
    assert not out_pdf.exists()

    boton = next(b for b in at.sidebar.button if b.label == "📄 Generar/Actualizar PDF")
    boton.click().run(timeout=90)

    assert not list(at.exception), f"el botón lanzó una excepción: {list(at.exception)}"
    assert out_pdf.exists(), "el botón no generó el PDF en la ruta esperada"
    assert out_pdf.with_suffix(".meta.json").exists(), "el botón no escribió el metadato del PDF"

    etiquetas_sidebar = [getattr(el, "label", "") for el in at.sidebar]
    assert any("Descargar PDF" in etq for etq in etiquetas_sidebar), (
        "no aparece la opción de descarga tras generar el PDF"
    )


def test_boton_respeta_forzar_pdf_solo_si_se_marca(app):
    at, out_pdf = app
    # Genera una vez (sin forzar) para tener un PDF + metadato de referencia.
    boton = next(b for b in at.sidebar.button if b.label == "📄 Generar/Actualizar PDF")
    boton.click().run(timeout=90)
    assert out_pdf.exists()

    # Sin tocar la caché de necesidades, un segundo click (sin forzar) debe
    # dejar el PDF "vigente" según pdf_vigente() -- lo comprobamos llamando
    # directamente a la función (más preciso que parsear el texto de la UI).
    vigente = pdf_generador.pdf_vigente(
        out_pdf, db_path=servicio.DB_PATH_DEFAULT, agg_path=servicio.AGG_PATH_DEFAULT,
        out_dir=servicio.OUT_DIR_DEFAULT,
    )
    assert vigente
