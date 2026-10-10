"""PDF determinista (sin IA) del reporte de las 10 necesidades analíticas (Fase 24).

Regla central (Fase 4, no reabrir): este módulo NUNCA llama a
`src.analytics.*.calcular()` en vivo. Todo dato sale de `src.dashboard.
servicio` (`cargar_necesidad`/`refrescar_todas`) -- el mismo mecanismo de
caché que ya usa `src/ui/dashboard.py`. Si algo no está en caché, se
calcula una vez con ESE mecanismo, nunca con una ruta paralela.

Las gráficas se dibujan con matplotlib (imágenes estáticas) a partir de las
MISMAS funciones de `src.dashboard.graficas_datos` que usa el dashboard de
Streamlit (Altair) -- ningún DataFrame se vuelve a seleccionar aquí.

CLI:
    python -m src.pdf.generador --db data/instacart.duckdb --out data/pdf/reporte_instacart.pdf [--forzar]
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as _escapar_xml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import matplotlib  # noqa: E402

matplotlib.use("Agg")  # sin display: genera PNG directo a memoria
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    BaseDocTemplate,
    Frame,
    Image as RLImage,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents  # noqa: E402

from scripts.generar_ficha_dataset import Ficha, construir_ficha  # noqa: E402
from src.analytics.conexion import abrir_conexion  # noqa: E402
from src.analytics.contrato import ResultadoNecesidad  # noqa: E402
from src.analytics.necesidades import columnas_ordenadas  # noqa: E402
from src.analytics.registro import NECESIDADES  # noqa: E402
from src.dashboard import graficas_datos as gd  # noqa: E402
from src.dashboard import servicio  # noqa: E402

# ---------------------------------------------------------------------------
# Layout (todos los literales numéricos de diseño viven aquí, con nombre; el
# test de "cero cifras hardcodeadas" del PDF los deja pasar por estar en esta
# lista blanca explícita -- ver tests/test_pdf_generador.py).
# ---------------------------------------------------------------------------

MARGEN_CM = 2.0
ANCHO_PAGINA, ALTO_PAGINA = letter
ANCHO_CONTENIDO = ANCHO_PAGINA - 2 * MARGEN_CM * cm

TAMANO_FUENTE_TITULO = 22
TAMANO_FUENTE_H1 = 16
TAMANO_FUENTE_H2 = 13
TAMANO_FUENTE_NORMAL = 10
TAMANO_FUENTE_NOTA = 9
TAMANO_FUENTE_CODIGO = 7.5
TAMANO_FUENTE_TABLA = 8.5

GROSOR_BORDE_RECUADRO = 1
PADDING_RECUADRO = 6
ANCHO_IMAGEN_GRANDE_CM = 16.0

FIG_ANCHO_IN = 8.5
FIG_ALTO_IN = 4.4
FIG_ALTO_CHICO_IN = 3.4
FIG_ALTO_POR_BARRA_IN = 0.38
FILAS_TABLA_RESULTADO_MAX = 10
FIG_DPI = 150

COLOR_PRIMARIO = "#2a9d8f"
COLOR_SECUNDARIO = "#e76f51"
COLOR_NEUTRO = "#457b9d"
COLOR_CORTE = "#d62828"
COLOR_FONDO_LIMITACION = colors.Color(1.0, 0.965, 0.82)  # amarillo suave
COLOR_BORDE_LIMITACION = colors.Color(0.80, 0.62, 0.0)
COLOR_FONDO_TABLA_HEADER = colors.Color(0.89, 0.93, 0.94)

NECESIDADES_ORDEN = list(NECESIDADES)

DB_PATH_DEFAULT = servicio.DB_PATH_DEFAULT
OUT_DIR_DEFAULT = servicio.OUT_DIR_DEFAULT
OUT_PDF_DEFAULT = Path("data/pdf/reporte_instacart.pdf")
RAW_DIR_DEFAULT = Path("data/instacart/raw")

CAMPOS_HUELLA_BD = ("orders_total", "orders_test", "order_products_prior", "order_products_train")
TOLERANCIA_COMPARACION_PARAMETRO = 1e-9
BYTES_POR_KB = 1024


# ---------------------------------------------------------------------------
# Estilos
# ---------------------------------------------------------------------------

_BASE = getSampleStyleSheet()
ESTILO_TITULO = ParagraphStyle("TituloPortada", parent=_BASE["Title"], fontSize=TAMANO_FUENTE_TITULO, alignment=TA_CENTER)
ESTILO_H1 = ParagraphStyle("H1Necesidad", parent=_BASE["Heading1"], fontSize=TAMANO_FUENTE_H1, spaceBefore=TAMANO_FUENTE_H1, spaceAfter=TAMANO_FUENTE_NOTA)
ESTILO_H1_SIN_TOC = ParagraphStyle("H1SinTOC", parent=ESTILO_H1)
ESTILO_H2 = ParagraphStyle("H2Sub", parent=_BASE["Heading2"], fontSize=TAMANO_FUENTE_H2)
ESTILO_NORMAL = ParagraphStyle("NormalPDF", parent=_BASE["BodyText"], fontSize=TAMANO_FUENTE_NORMAL, leading=TAMANO_FUENTE_NORMAL + 3)
ESTILO_NOTA = ParagraphStyle("NotaPDF", parent=_BASE["BodyText"], fontSize=TAMANO_FUENTE_NOTA, leading=TAMANO_FUENTE_NOTA + 2, textColor=colors.Color(0.30, 0.30, 0.30))
ESTILO_LIMITACION = ParagraphStyle("LimitacionPDF", parent=_BASE["BodyText"], fontSize=TAMANO_FUENTE_NORMAL, leading=TAMANO_FUENTE_NORMAL + 4)
ESTILO_CODIGO_NOMBRE = ParagraphStyle("CodigoNombre", parent=_BASE["BodyText"], fontSize=TAMANO_FUENTE_NOTA, fontName="Helvetica-Bold")
ESTILO_CODIGO = ParagraphStyle("CodigoSQL", parent=_BASE["Code"], fontSize=TAMANO_FUENTE_CODIGO, leading=TAMANO_FUENTE_CODIGO + 2)
ESTILO_TOC_1 = ParagraphStyle("TOC1", parent=_BASE["Normal"], fontSize=TAMANO_FUENTE_NORMAL + 1, leading=TAMANO_FUENTE_NORMAL + 6)


def _p(texto: str, estilo: ParagraphStyle = ESTILO_NORMAL) -> Paragraph:
    return Paragraph(_escapar_xml(texto), estilo)


def _fmt_valor(valor: object, unidad: str) -> str:
    """Mismo criterio de formato que `src/ui/dashboard.py::_fmt_valor` --
    duplicado a propósito (es formato de texto, no selección de datos: lo
    que el brief pide no duplicar es la selección de DataFrames, ver
    `src.dashboard.graficas_datos`) para que el PDF no dependa de importar
    `streamlit` (que trae `dashboard.py`) solo para formatear un número."""
    if valor is None:
        return "-"
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if unidad == "fracción" and isinstance(valor, (int, float)):
        return f"{valor:.1%}"
    if isinstance(valor, float):
        return f"{valor:,.3f}" if abs(valor) < 10 else f"{valor:,.2f}"
    if isinstance(valor, int):
        return f"{valor:,}"
    if isinstance(valor, list):
        return ", ".join(str(v) for v in valor)
    return str(valor)


def _figura_a_imagen(fig: Figure, ancho_cm: float) -> RLImage:
    """Convierte una `Figure` de matplotlib en un flowable `Image` de
    reportlab, escalado a `ancho_cm` manteniendo la proporción real del PNG
    (medida del propio archivo, no supuesta a partir del `figsize`, que
    `bbox_inches='tight'` recorta)."""
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=FIG_DPI, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    ancho_px, alto_px = ImageReader(buffer).getSize()
    ancho = ancho_cm * cm
    alto = ancho * alto_px / ancho_px
    buffer.seek(0)
    return RLImage(buffer, width=ancho, height=alto)


# ---------------------------------------------------------------------------
# Gráficas (matplotlib), una función por necesidad, sobre los MISMOS
# DataFrames que arma `src.dashboard.graficas_datos` para el panel Altair
# equivalente en `src/ui/dashboard.py`.
# ---------------------------------------------------------------------------

def _fig_vacia(titulo: str) -> Figure:
    fig, ax = plt.subplots(figsize=(FIG_ANCHO_IN, FIG_ALTO_CHICO_IN))
    ax.text(0.5, 0.5, "Sin datos suficientes para esta gráfica", ha="center", va="center", fontsize=TAMANO_FUENTE_NORMAL)
    ax.set_title(titulo)
    ax.axis("off")
    return fig


def _fig_barras(r: ResultadoNecesidad) -> Figure | None:
    """Barras del ranking de la necesidad (mismo DataFrame que el panel
    Altair, `gd.datos_grafica`). None si la necesidad es de tipo escalar."""
    datos = gd.datos_grafica(r)
    if datos is None:
        return None
    if datos.df.empty:
        return _fig_vacia(datos.titulo)
    df = datos.df.iloc[::-1]  # la primera fila arriba
    n = len(df)
    fig, ax = plt.subplots(figsize=(FIG_ANCHO_IN, max(FIG_ALTO_CHICO_IN, FIG_ALTO_POR_BARRA_IN * n)))
    colores = [COLOR_PRIMARIO if (n - 1 - k) < datos.filas_respuesta else COLOR_NEUTRO for k in range(n)]
    ax.barh(df[datos.categoria].astype(str), df[datos.valor], color=colores)
    ax.set_xlabel(datos.valor)
    ax.set_title(datos.titulo, fontsize=TAMANO_FUENTE_NORMAL)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Documento con índice (TableOfContents): reportlab arma el índice con
# número de página real en una segunda pasada automática (`multiBuild`),
# capturando cada párrafo de estilo `ESTILO_H1` en `afterFlowable`.
# ---------------------------------------------------------------------------

class _DocumentoConIndice(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style is ESTILO_H1:
            texto = flowable.getPlainText()
            self.notify("TOCEntry", (0, texto, self.page))


def _recuadro(contenido, color_fondo, color_borde) -> Table:
    tabla = Table([[contenido]], colWidths=[ANCHO_CONTENIDO])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color_fondo),
        ("BOX", (0, 0), (-1, -1), GROSOR_BORDE_RECUADRO, color_borde),
        ("LEFTPADDING", (0, 0), (-1, -1), PADDING_RECUADRO),
        ("RIGHTPADDING", (0, 0), (-1, -1), PADDING_RECUADRO),
        ("TOPPADDING", (0, 0), (-1, -1), PADDING_RECUADRO),
        ("BOTTOMPADDING", (0, 0), (-1, -1), PADDING_RECUADRO),
    ]))
    return tabla


def _recuadro_limitaciones(limitaciones: list[str]) -> Table:
    texto = "<br/><br/>".join(f"⚠ {_escapar_xml(l)}" for l in limitaciones)
    return _recuadro(Paragraph(texto, ESTILO_LIMITACION), COLOR_FONDO_LIMITACION, COLOR_BORDE_LIMITACION)


def _tabla_valores_clave(r: ResultadoNecesidad) -> Table:
    datos = [["Valor clave", "Valor", "Unidad"]]
    for vc in r.valores_clave:
        datos.append([vc.etiqueta, _fmt_valor(vc.valor, vc.unidad), vc.unidad])
    tabla = Table(datos, colWidths=[ANCHO_CONTENIDO * 0.55, ANCHO_CONTENIDO * 0.25, ANCHO_CONTENIDO * 0.20])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_FONDO_TABLA_HEADER),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), TAMANO_FUENTE_TABLA),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return tabla


def _tabla_resultado(r: ResultadoNecesidad) -> Table:
    """Las filas de la consulta de referencia, tal cual (hasta
    `FILAS_TABLA_RESULTADO_MAX`)."""
    filas = next(iter(r.tablas.values()), [])
    if not filas:
        return Table([["(la consulta no devolvió filas)"]], colWidths=[ANCHO_CONTENIDO])
    columnas = columnas_ordenadas(r.id, filas)
    datos = [columnas] + [[Paragraph(_escapar_xml(_fmt_valor(f[c], "")), ESTILO_NOTA) for c in columnas]
                          for f in filas[:FILAS_TABLA_RESULTADO_MAX]]
    tabla = Table(datos, colWidths=[ANCHO_CONTENIDO / len(columnas)] * len(columnas))
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_FONDO_TABLA_HEADER),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), TAMANO_FUENTE_TABLA),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return tabla


def _seccion_portada(ficha: Ficha, resultados: dict[str, ResultadoNecesidad], fecha_generacion: str) -> list:
    story = [
        Paragraph("Reporte analítico Instacart", ESTILO_TITULO),
        Spacer(1, TAMANO_FUENTE_H1),
        Paragraph(f"Generado el {fecha_generacion}", ESTILO_NOTA),
        Spacer(1, TAMANO_FUENTE_H1),
        _recuadro(
            Paragraph(
                "<b>Generado sin intervención de IA:</b> cada cifra de este documento proviene de una "
                "consulta SQL ejecutada contra la base de datos (ver el Apéndice de consultas SQL). "
                "El texto de hallazgos y limitaciones lo produce el código de <font face=\"Courier\">"
                "src.analytics</font>, no un modelo de lenguaje.",
                ESTILO_LIMITACION,
            ),
            COLOR_FONDO_LIMITACION, COLOR_BORDE_LIMITACION,
        ),
        Spacer(1, TAMANO_FUENTE_H1),
        Paragraph("Ficha del dataset", ESTILO_H2),
        Paragraph(f"<b>Fuente:</b> {_escapar_xml(ficha.fuente)}", ESTILO_NORMAL),
        Paragraph(f"<b>Copia usada:</b> {_escapar_xml(ficha.copia_usada)}", ESTILO_NORMAL),
        Paragraph(f"<b>Licencia:</b> {_escapar_xml(ficha.licencia)}", ESTILO_NORMAL),
        Spacer(1, TAMANO_FUENTE_NOTA),
    ]
    datos_dim = [["Tabla", "Filas"]] + [[d.nombre, f"{d.filas:,}"] for d in ficha.dimensiones]
    tabla_dim = Table(datos_dim, colWidths=[ANCHO_CONTENIDO * 0.7, ANCHO_CONTENIDO * 0.3])
    tabla_dim.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_FONDO_TABLA_HEADER),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), TAMANO_FUENTE_TABLA),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(tabla_dim)
    story.append(Spacer(1, TAMANO_FUENTE_NOTA))
    story.append(Paragraph(
        f"Validación (`src.db.instacart_validacion`): {ficha.n_pass} PASS, {ficha.n_fail} FAIL, "
        f"{ficha.n_info} INFO. DuckDB {ficha.version_duckdb}.", ESTILO_NOTA,
    ))
    story.append(Spacer(1, TAMANO_FUENTE_H1))
    story.append(Paragraph("Limitaciones del dataset", ESTILO_H2))
    for lim in ficha.limitaciones:
        story.append(Paragraph(f"• {_escapar_xml(lim)}", ESTILO_NOTA))
    story.append(Spacer(1, TAMANO_FUENTE_H1))

    story.append(Paragraph("Huella de la base de datos fuente, por necesidad", ESTILO_H2))
    story.append(Paragraph(
        "Los conteos de `orders`/`order_products` guardados junto con cada necesidad en el momento en "
        "que se calculó -- si difieren entre necesidades, no vienen de la misma foto de la base de datos.",
        ESTILO_NOTA,
    ))
    encabezado_huella = ["Necesidad"] + [c for c in CAMPOS_HUELLA_BD]
    filas_huella = [encabezado_huella]
    for nid in NECESIDADES_ORDEN:
        hb = resultados[nid].huella_bd
        filas_huella.append([nid] + [f"{getattr(hb, campo):,}" for campo in CAMPOS_HUELLA_BD])
    tabla_huella = Table(filas_huella, colWidths=[ANCHO_CONTENIDO * 0.12] + [ANCHO_CONTENIDO * 0.22] * 4)
    tabla_huella.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_FONDO_TABLA_HEADER),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), TAMANO_FUENTE_TABLA),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(tabla_huella)
    story.append(PageBreak())
    return story


def _seccion_indice() -> list:
    toc = TableOfContents()
    toc.levelStyles = [ESTILO_TOC_1]
    return [
        Paragraph("Índice", ESTILO_H1_SIN_TOC),
        toc,
        PageBreak(),
    ]


def _seccion_necesidad(r: ResultadoNecesidad) -> list:
    story = [
        Paragraph(f"{r.id} — {r.titulo}", ESTILO_H1),
        Paragraph(f"<b>Pregunta:</b> {_escapar_xml(r.pregunta)}", ESTILO_NORMAL),
        Paragraph(f"<b>Decisión:</b> {_escapar_xml(r.decision)}", ESTILO_NORMAL),
        Paragraph(f"<b>Universo:</b> {_escapar_xml(r.universo.texto)}", ESTILO_NORMAL),
    ]
    if r.universo.filtros:
        story.append(Paragraph("<b>Filtros:</b> " + _escapar_xml("; ".join(r.universo.filtros)), ESTILO_NOTA))
    story.append(Spacer(1, TAMANO_FUENTE_NOTA))

    story.append(Paragraph("Respuesta", ESTILO_H2))
    for h in r.hallazgos:
        story.append(Paragraph(f"• {_escapar_xml(h)}", ESTILO_NORMAL))
    story.append(Spacer(1, TAMANO_FUENTE_NOTA))

    fig = _fig_barras(r)
    if fig is not None:
        story.append(_figura_a_imagen(fig, ANCHO_IMAGEN_GRANDE_CM))
        story.append(Spacer(1, TAMANO_FUENTE_NOTA))

    story.append(Paragraph("Valores clave", ESTILO_H2))
    story.append(_tabla_valores_clave(r))
    story.append(Spacer(1, TAMANO_FUENTE_NOTA))

    story.append(Paragraph("Resultado crudo de la consulta", ESTILO_H2))
    story.append(_tabla_resultado(r))
    story.append(Spacer(1, TAMANO_FUENTE_NOTA))

    story.append(Paragraph("Limitaciones", ESTILO_H2))
    story.append(_recuadro_limitaciones(r.limitaciones))
    story.append(PageBreak())
    return story


def _seccion_apendice_sql(resultados: dict[str, ResultadoNecesidad]) -> list:
    story = [Paragraph("Apéndice: consultas SQL ejecutadas", ESTILO_H1)]
    story.append(Paragraph(
        "Tal cual las ejecutó `src.analytics` para producir cada necesidad -- ninguna se reescribe ni "
        "se resume para este documento.", ESTILO_NOTA,
    ))
    for nid in NECESIDADES_ORDEN:
        r = resultados[nid]
        story.append(Paragraph(f"{nid} — {r.titulo}", ESTILO_H2))
        for consulta in r.consultas:
            story.append(Paragraph(consulta.nombre, ESTILO_CODIGO_NOMBRE))
            story.append(Preformatted(consulta.sql, ESTILO_CODIGO))
            story.append(Spacer(1, TAMANO_FUENTE_NOTA / 2))
    return story


def _seccion_parametros(resultados: dict[str, ResultadoNecesidad], discrepancias: list[str]) -> list:
    story = [PageBreak(), Paragraph("Parámetros de cada necesidad", ESTILO_H1)]
    story.append(Paragraph(
        "Valor y justificación medida de cada parámetro, leídos directamente del campo `parametros` "
        "del JSON de cada necesidad (fuente de verdad; ver `docs/NECESIDADES.md` para la versión narrada).",
        ESTILO_NOTA,
    ))
    if discrepancias:
        texto = "<br/>".join(_escapar_xml(d) for d in discrepancias)
        story.append(_recuadro(
            Paragraph("<b>Discrepancias encontradas entre el JSON y docs/NECESIDADES.md:</b><br/>" + texto, ESTILO_LIMITACION),
            COLOR_FONDO_LIMITACION, COLOR_BORDE_LIMITACION,
        ))
    else:
        story.append(Paragraph(
            "No se encontraron discrepancias entre los parámetros tabulados en docs/NECESIDADES.md y "
            "los valores del JSON de cada necesidad.", ESTILO_NOTA,
        ))
    story.append(Spacer(1, TAMANO_FUENTE_NOTA))

    for nid in NECESIDADES_ORDEN:
        r = resultados[nid]
        story.append(Paragraph(f"{nid} — {r.titulo}", ESTILO_H2))
        datos = [["Parámetro", "Valor", "Justificación medida"]]
        for p in r.parametros:
            datos.append([p.nombre, _fmt_valor(p.valor, ""), p.justificacion])
        tabla = Table(datos, colWidths=[ANCHO_CONTENIDO * 0.18, ANCHO_CONTENIDO * 0.14, ANCHO_CONTENIDO * 0.68])
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_FONDO_TABLA_HEADER),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), TAMANO_FUENTE_TABLA),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tabla)
        story.append(Spacer(1, TAMANO_FUENTE_NOTA))
    return story


# ---------------------------------------------------------------------------
# Verificación cruzada JSON vs. docs/NECESIDADES.md (punto 2e del prompt: "el
# JSON manda si hay discrepancia, repórtala si la encuentras"). Best-effort:
# solo compara parámetros que SÍ aparecen tabulados en el markdown (`|
# `NOMBRE` | valor | ... |`); uno no tabulado ahí no es una discrepancia, es
# "no verificable con este parseo".
# ---------------------------------------------------------------------------

_PATRON_FILA_PARAMETRO_MD = re.compile(r"\|\s*`([A-Za-z][A-Za-z0-9_]*)`\s*\|\s*([^|]+?)\s*\|")


def _normalizar_valor_texto(texto: str) -> str:
    return re.sub(r"[\s,()\[\]]+", "", texto).lower()


def _valores_coinciden(valor_json_texto: str, valor_md_texto: str) -> bool:
    """Compara dos representaciones de texto del mismo parámetro. Números
    se comparan por valor (así "2.0" == "2.000" no se marca como
    discrepancia por puro formato); todo lo demás, como texto normalizado
    (sin espacios/comas/paréntesis, para listas como "(5, 10)" vs "5, 10").
    Un valor del markdown que no es una cifra propiamente tabulada (p. ej.
    una frase descriptiva como "P99 del tamaño de pedido (35)") nunca
    "coincide" por texto -- se reporta como discrepancia; en la práctica
    esos casos son parámetros que `NECESIDADES.md` narra en vez de tabular
    con un valor exacto, ver `docs/FASE4_REPORTE.md`."""
    try:
        return abs(float(valor_json_texto.replace(",", "")) - float(valor_md_texto.replace(",", ""))) < TOLERANCIA_COMPARACION_PARAMETRO
    except ValueError:
        pass
    return _normalizar_valor_texto(valor_json_texto) == _normalizar_valor_texto(valor_md_texto)


def verificar_parametros_contra_necesidades_md(
    resultados: dict[str, ResultadoNecesidad], ruta_md: Path,
) -> list[str]:
    if not ruta_md.exists():
        return [f"No se encontró {ruta_md} para verificar parámetros."]

    texto_md = ruta_md.read_text(encoding="utf-8")
    valores_md: dict[str, list[str]] = {}
    for nombre, valor_texto in _PATRON_FILA_PARAMETRO_MD.findall(texto_md):
        valores_md.setdefault(nombre, []).append(valor_texto.strip())

    discrepancias: list[str] = []
    for nid in NECESIDADES_ORDEN:
        for p in resultados[nid].parametros:
            candidatos = valores_md.get(p.nombre)
            if not candidatos:
                continue
            valor_json_texto = _fmt_valor(p.valor, "")
            if not any(_valores_coinciden(valor_json_texto, c) for c in candidatos):
                discrepancias.append(
                    f"{nid}.{p.nombre}: JSON={valor_json_texto!r}, docs/NECESIDADES.md={candidatos!r}"
                )
    return discrepancias


# ---------------------------------------------------------------------------
# Metadato junto al PDF (Fase 4, punto 4): qué huella_bd tenía cada
# necesidad cuando se generó ESTE PDF, para que el dashboard sepa si sigue
# vigente sin tener que abrir el PDF.
# ---------------------------------------------------------------------------

def _ruta_metadato(ruta_pdf: Path) -> Path:
    return ruta_pdf.with_suffix(".meta.json")


def _huella_bd_a_dict(r: ResultadoNecesidad) -> dict[str, int]:
    return {campo: getattr(r.huella_bd, campo) for campo in CAMPOS_HUELLA_BD}


def leer_metadato(ruta_pdf: Path) -> dict | None:
    ruta = _ruta_metadato(ruta_pdf)
    if not ruta.exists():
        return None
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _escribir_metadato(ruta_pdf: Path, resultados: dict[str, ResultadoNecesidad], fecha_generacion: str) -> None:
    metadato = {
        "fecha_generacion": fecha_generacion,
        "huella_por_necesidad": {nid: _huella_bd_a_dict(resultados[nid]) for nid in NECESIDADES_ORDEN},
    }
    _ruta_metadato(ruta_pdf).write_text(json.dumps(metadato, indent=2, ensure_ascii=False), encoding="utf-8")


def pdf_vigente(
    ruta_pdf: Path,
    *,
    db_path: Path | None = None,
    out_dir: Path | None = None,
) -> bool:
    """True si el PDF ya existe y la huella_bd de las 10 necesidades EN
    CACHÉ coincide con la que tenía cuando se generó ese PDF (no recalcula
    nada: usa `cargar_necesidad`, que sirve de `data/referencia/*.json` si
    ya está vigente). `None` se resuelve en la llamada, mismo motivo que en
    `generar_pdf`."""
    db_path = db_path if db_path is not None else DB_PATH_DEFAULT
    out_dir = out_dir if out_dir is not None else OUT_DIR_DEFAULT
    if not ruta_pdf.exists():
        return False
    metadato = leer_metadato(ruta_pdf)
    if metadato is None:
        return False

    con = abrir_conexion(db_path)
    try:
        for nid in NECESIDADES_ORDEN:
            carga = servicio.cargar_necesidad(nid, db_path=db_path, out_dir=out_dir, con=con)
            actual = _huella_bd_a_dict(carga.resultado)
            guardada = metadato.get("huella_por_necesidad", {}).get(nid)
            if guardada != actual:
                return False
    finally:
        con.close()
    return True


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

@dataclass
class ResumenPDF:
    ruta: Path
    duracion_s: float
    tamano_bytes: int
    discrepancias_parametros: list[str] = field(default_factory=list)
    fecha_generacion: str = ""


def generar_pdf(
    *,
    db_path: Path | None = None,
    out_dir: Path | None = None,
    out_pdf: Path | None = None,
    raw_dir: Path | None = None,
    forzar: bool = False,
    necesidades_md: Path | None = None,
) -> ResumenPDF:
    """Arma el PDF completo. `forzar=True` recalcula las 10 necesidades
    antes de armar el documento (mismo mecanismo que el botón "Actualizar
    datos" del dashboard); sin `forzar`, cada necesidad se sirve de caché
    si ya está vigente y solo se recalcula la que falte o esté rancia
    (exactamente lo que ya hace `cargar_necesidad`).

    Los `None` se resuelven contra los defaults del módulo EN EL MOMENTO DE
    LA LLAMADA (mismo motivo que en `servicio.cargar_necesidad`, ver Fase 3:
    permite que un test monkeypatchee `generador.DB_PATH_DEFAULT`/etc. y que
    tanto llamadas directas como el botón del dashboard usen la ruta
    parcheada, sin depender de un default ya congelado al definir la
    función)."""
    db_path = db_path if db_path is not None else DB_PATH_DEFAULT
    out_dir = out_dir if out_dir is not None else OUT_DIR_DEFAULT
    out_pdf = out_pdf if out_pdf is not None else OUT_PDF_DEFAULT
    raw_dir = raw_dir if raw_dir is not None else RAW_DIR_DEFAULT
    necesidades_md = necesidades_md if necesidades_md is not None else Path("docs/NECESIDADES.md")
    t_inicio = time.perf_counter()

    if forzar:
        servicio.refrescar_todas(forzar=True, db_path=db_path, out_dir=out_dir)

    con = abrir_conexion(db_path)
    try:
        resultados: dict[str, ResultadoNecesidad] = {}
        for nid in NECESIDADES_ORDEN:
            carga = servicio.cargar_necesidad(nid, db_path=db_path, out_dir=out_dir, con=con)
            resultados[nid] = carga.resultado
        ficha = construir_ficha(con, db_path, raw_dir)
    finally:
        con.close()

    fecha_generacion = datetime.now(timezone.utc).isoformat()
    discrepancias = verificar_parametros_contra_necesidades_md(resultados, necesidades_md)

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc = _DocumentoConIndice(
        str(out_pdf), pagesize=letter,
        leftMargin=MARGEN_CM * cm, rightMargin=MARGEN_CM * cm,
        topMargin=MARGEN_CM * cm, bottomMargin=MARGEN_CM * cm,
        title="Reporte analítico Instacart", author="src.pdf.generador (sin IA)",
    )
    marco = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="marco")
    doc.addPageTemplates([PageTemplate(id="pagina", frames=[marco])])

    story: list = []
    story += _seccion_portada(ficha, resultados, fecha_generacion)
    story += _seccion_indice()
    for nid in NECESIDADES_ORDEN:
        story += _seccion_necesidad(resultados[nid])
    story += _seccion_apendice_sql(resultados)
    story += _seccion_parametros(resultados, discrepancias)

    doc.multiBuild(story)

    _escribir_metadato(out_pdf, resultados, fecha_generacion)

    return ResumenPDF(
        ruta=out_pdf,
        duracion_s=time.perf_counter() - t_inicio,
        tamano_bytes=out_pdf.stat().st_size,
        discrepancias_parametros=discrepancias,
        fecha_generacion=fecha_generacion,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.pdf.generador")
    parser.add_argument("--db", default=str(DB_PATH_DEFAULT))
    parser.add_argument("--out", default=str(OUT_PDF_DEFAULT))
    parser.add_argument("--raw", default=str(RAW_DIR_DEFAULT))
    parser.add_argument("--forzar", action="store_true")
    args = parser.parse_args(argv)

    resumen = generar_pdf(
        db_path=Path(args.db), out_pdf=Path(args.out),
        raw_dir=Path(args.raw), forzar=args.forzar,
    )
    print(f"PDF escrito en {resumen.ruta} ({resumen.tamano_bytes / BYTES_POR_KB:,.0f} KB) en {resumen.duracion_s:.2f}s")
    if resumen.discrepancias_parametros:
        print(f"ADVERTENCIA: {len(resumen.discrepancias_parametros)} discrepancias entre JSON y NECESIDADES.md:")
        for d in resumen.discrepancias_parametros:
            print(f"  - {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
