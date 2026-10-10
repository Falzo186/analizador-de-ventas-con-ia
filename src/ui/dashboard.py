"""Dashboard de las 10 necesidades analíticas de Instacart (Fase 3; Fase 24).

Para correrla (standalone, aislada del chat):

    streamlit run src/ui/dashboard.py

Fase 6: el mismo dashboard se monta en el panel derecho de
`src/ui/app_instacart.py`. Para poder importarlo sin ejecutarlo, el código
de nivel de módulo pasó a funciones (`render_controles`, `render_panel`,
`main`). Standalone se comporta igual que antes: `main()` pone los controles
en la barra lateral y el panel en el área principal.

Esta UI NO tiene lógica de negocio propia y NUNCA llama a
`src.analytics.*.calcular()` ni a `src.analytics.registro.invocar()`
directamente: todo pasa por `src.dashboard.servicio`, que sirve los
resultados ya precalculados en `data/referencia/*.json` (con caché
validada contra la huella de la BD fuente) y solo recalcula cuando hace
falta. La más lenta (N2, ~5 s) ya es demasiado para cada rerun de
Streamlit -- por eso el botón "Actualizar datos" es la única vía para
disparar un recálculo, nunca un render de panel.

Cero IA, cero cifras hardcodeadas (todo sale de `ResultadoNecesidad`), cero
lenguaje de dinero (el propio contenido de `src.analytics` ya lo evita).
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run` solo agrega al sys.path la carpeta del script (src/ui/),
# no la raíz del proyecto -- la añadimos para poder hacer `import src...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import altair as alt
import pandas as pd
import streamlit as st

from src.analytics.contrato import ResultadoNecesidad
from src.analytics.necesidades import columnas_ordenadas
from src.analytics.registro import NECESIDADES
from src.dashboard import graficas_datos as gd
from src.dashboard import servicio
from src.pdf import generador as pdf_generador

NECESIDADES_IDS = list(NECESIDADES)


# ---------------------------------------------------------------------------
# Carga con caché de sesión de Streamlit sobre la caché en disco de servicio.py
# (evita reabrir conexión + recomprobar huella en cada rerun de Streamlit,
# que ocurre en cada interacción de un widget, no solo al cambiar de panel).
# ---------------------------------------------------------------------------

def inicializar_estado() -> None:
    if "version_cache" not in st.session_state:
        st.session_state["version_cache"] = 0
    if "ultimo_refresco" not in st.session_state:
        st.session_state["ultimo_refresco"] = None
    if "ultimo_pdf" not in st.session_state:
        st.session_state["ultimo_pdf"] = None


@st.cache_data(show_spinner="Cargando necesidad...")
def _cargar_cacheado(necesidad_id: str, version: int) -> servicio.CargaNecesidad:
    # `version` participa en la clave de caché de Streamlit a propósito: al
    # incrementarla (botón "Actualizar datos") invalida esta caché de sesión
    # sin tocar la caché en disco, que servicio.py ya gobierna por su cuenta.
    return servicio.cargar_necesidad(necesidad_id)


def cargar(necesidad_id: str) -> servicio.CargaNecesidad:
    return _cargar_cacheado(necesidad_id, st.session_state["version_cache"])


@st.cache_data(show_spinner=False)
def _pdf_vigente_cacheado(version: int) -> bool:
    # Mismo motivo que `_cargar_cacheado`: `pdf_vigente()` abre su propia
    # conexión y llama a `cargar_necesidad` x10 (barato con caché tibia,
    # ~0.2s medido, pero no gratis en CADA rerun de Streamlit).
    return pdf_generador.pdf_vigente(pdf_generador.OUT_PDF_DEFAULT)


# ---------------------------------------------------------------------------
# Formato (sin inventar cifras: solo da forma a lo que ya trae el JSON)
# ---------------------------------------------------------------------------

def _fmt_valor(valor: object, unidad: str) -> str:
    if valor is None:
        return "—"
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if unidad == "fracción" and isinstance(valor, (int, float)):
        return f"{valor:.1%}"
    if isinstance(valor, float):
        return f"{valor:,.3f}" if abs(valor) < 10 else f"{valor:,.2f}"
    if isinstance(valor, int):
        return f"{valor:,}"
    return str(valor)


def _df(tabla: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(tabla)


def _seguro(df: pd.DataFrame, construir):
    """Devuelve la gráfica que arma `construir(df)`, o `None` si `df` está
    vacío. Un DataFrame sin filas hace que Altair no pueda inferir el tipo
    de sus columnas (`Unable to determine data type for the field...`) y
    lance una excepción en vez de mostrar "sin datos" -- puede pasar con
    universos muy chicos (parámetros exigentes, o una fixture de prueba),
    no solo en producción con el dataset completo. El panel debe degradar
    con un aviso, no romperse."""
    if df.empty:
        return None
    return construir(df)


# ---------------------------------------------------------------------------
# Gráfica de la necesidad (Fase 24: una genérica para las 10). Recibe el
# ResultadoNecesidad ya cargado y usa SOLO su tabla (`graficas_datos`): no
# calcula ni inventa datos. Devuelve [(título, gráfica | None)]; `None`
# significa "sin datos suficientes" (ver `_seguro`). Las necesidades de tipo
# escalar no tienen gráfica: lista vacía.
# ---------------------------------------------------------------------------

def graficos(r: ResultadoNecesidad) -> list:
    datos = gd.datos_grafica(r)
    if datos is None:
        return []
    df = datos.df.reset_index(drop=True)
    if not df.empty:
        df["respuesta"] = ["Responde la pregunta" if i < datos.filas_respuesta else "Contexto"
                           for i in range(len(df))]

    def _construir(d):
        return alt.Chart(d).mark_bar().encode(
            x=alt.X(f"{datos.valor}:Q", title=datos.valor),
            y=alt.Y(f"{datos.categoria}:N", sort="-x" if d[datos.valor].iloc[0] >= d[datos.valor].iloc[-1] else "x",
                    title=datos.categoria),
            color=alt.Color("respuesta:N", title=""),
            tooltip=list(d.columns),
        ).properties(height=max(160, 28 * len(d)))

    return [(datos.titulo, _seguro(df, _construir))]

# ---------------------------------------------------------------------------
# Sidebar: selector de necesidad + botón global de actualización
# ---------------------------------------------------------------------------

def render_controles(area=st.sidebar, *, horizontal: bool = False) -> str:
    """Selector de necesidad, botón "Actualizar datos" y bloque del PDF,
    dibujados en `area` (la barra lateral en standalone, un contenedor del
    panel derecho en la app de dos paneles). Devuelve la necesidad elegida."""
    area.title("📊 Dashboard Instacart")
    area.caption(
        f"{len(NECESIDADES_IDS)} necesidades de una sola consulta sobre datos reales de Instacart, sin IA. "
        "Sirve resultados precalculados; nunca recalcula al cambiar de panel."
    )

    necesidad_sel = area.radio("Necesidad", NECESIDADES_IDS, key="necesidad_sel", horizontal=horizontal)

    area.divider()
    forzar = area.checkbox(
        "Forzar recálculo completo (ignora la caché en disco)",
        value=False,
        help="Sin marcar, solo recalcula lo que falte o esté rancio (huella_bd distinta a la BD fuente actual).",
    )
    if area.button("🔄 Actualizar datos", use_container_width=True):
        barra = area.progress(0.0, text="Actualizando…")
        contador = {"i": 0}

        def _avance(estado: servicio.EstadoNecesidad) -> None:
            contador["i"] += 1
            etiqueta = "recalculada" if estado.recalculada else "ya vigente"
            if estado.error:
                etiqueta = f"ERROR: {estado.error}"
            barra.progress(contador["i"] / len(NECESIDADES_IDS), text=f"{estado.id}: {etiqueta} ({estado.duracion_s:.2f}s)")

        resumen = servicio.refrescar_todas(forzar=forzar, al_terminar_cada_una=_avance)
        st.session_state["ultimo_refresco"] = resumen
        st.session_state["version_cache"] += 1  # invalida la caché de sesión de Streamlit (no la de disco)
        _cargar_cacheado.clear()

    ultimo = st.session_state["ultimo_refresco"]
    if ultimo is not None:
        area.success(
            f"Último refresco: {ultimo.n_recalculadas}/{len(ultimo.estados)} recalculadas en "
            f"{ultimo.duracion_total_s:.2f}s ({ultimo.n_errores} errores)."
        )
        with area.expander("Detalle del último refresco"):
            for e in ultimo.estados:
                marca = "🔁 recalculada" if e.recalculada else "✅ ya vigente"
                if e.error:
                    marca = "❌ error"
                st.write(f"**{e.id}** — {marca} ({e.duracion_s:.2f}s)" + (f" — {e.error}" if e.error else ""))


    # ---------------------------------------------------------------------------
    # PDF: mismo mecanismo de caché que las necesidades -- este botón nunca
    # recalcula una necesidad por su cuenta, solo llama a `pdf_generador.
    # generar_pdf()`, que a su vez pasa por `servicio.cargar_necesidad()` para
    # cada una (Fase 4, decisión ya tomada: no reabrir).
    # ---------------------------------------------------------------------------

    area.divider()
    area.subheader("📄 Reporte PDF")

    ruta_pdf = pdf_generador.OUT_PDF_DEFAULT
    pdf_existe = ruta_pdf.exists()
    pdf_vigente = _pdf_vigente_cacheado(st.session_state["version_cache"]) if pdf_existe else False

    if not pdf_existe:
        area.caption("Aún no se generó ningún PDF.")
    elif pdf_vigente:
        area.caption("✅ El PDF ya está actualizado: ninguna necesidad cambió desde que se generó.")
    else:
        area.caption("⚠️ El PDF existe, pero al menos una necesidad cambió desde que se generó (huella_bd distinta).")

    forzar_pdf = area.checkbox(
        f"Recalcular las {len(NECESIDADES_IDS)} necesidades antes de generar el PDF",
        value=False, key="forzar_pdf",
        help="Sin marcar, el PDF usa la caché de cada necesidad tal cual esté (igual que un panel); "
             "solo recalcula lo que falte o esté rancio.",
    )

    if area.button("📄 Generar/Actualizar PDF", use_container_width=True):
        with area.status("Generando PDF…", expanded=False) as status:
            resumen_pdf = pdf_generador.generar_pdf(out_pdf=ruta_pdf, forzar=forzar_pdf)
            status.update(
                label=f"PDF listo: {resumen_pdf.tamano_bytes / pdf_generador.BYTES_POR_KB:,.0f} KB en {resumen_pdf.duracion_s:.1f}s",
                state="complete",
            )
        st.session_state["ultimo_pdf"] = resumen_pdf
        st.session_state["version_cache"] += 1  # invalida _pdf_vigente_cacheado y _cargar_cacheado
        _cargar_cacheado.clear()
        _pdf_vigente_cacheado.clear()
        pdf_existe = True

    ultimo_pdf = st.session_state["ultimo_pdf"]
    if ultimo_pdf is not None:
        area.success(
            f"Generado: {ultimo_pdf.tamano_bytes / pdf_generador.BYTES_POR_KB:,.0f} KB en {ultimo_pdf.duracion_s:.1f}s."
        )
        if ultimo_pdf.discrepancias_parametros:
            with area.expander(f"⚠️ {len(ultimo_pdf.discrepancias_parametros)} discrepancias JSON vs. NECESIDADES.md"):
                for d in ultimo_pdf.discrepancias_parametros:
                    st.write(f"- {d}")

    if pdf_existe:
        with open(ruta_pdf, "rb") as _fh:
            _datos_pdf = _fh.read()
        area.download_button(
            "⬇️ Descargar PDF", data=_datos_pdf, file_name=ruta_pdf.name,
            mime="application/pdf", use_container_width=True,
        )

    return necesidad_sel


# ---------------------------------------------------------------------------
# Panel principal
# ---------------------------------------------------------------------------

def render_descarga_json(necesidad_sel: str) -> None:
    """Fase 10 (entregable de curso): descarga del `data/referencia/{id}.json`
    tal cual está en disco, solo para `servicio.NECESIDADES_ENTREGA`. Sin
    caché de sesión, a propósito: `servicio.json_para_descarga` comprueba la
    huella en cada ejecución (~0.1 s medido con caché tibia) y recalcula si el
    archivo falta o está rancio, así que nunca sirve un archivo desactualizado."""
    descarga = servicio.json_para_descarga(necesidad_sel)
    st.download_button(
        "⬇️ Descargar JSON de esta necesidad", data=descarga.contenido,
        file_name=descarga.nombre_archivo, mime="application/json", key=f"descargar_json_{necesidad_sel}",
        help="El JSON determinista del software (data/referencia/), sin IA, byte a byte como está en disco.",
    )
    if not descarga.desde_cache:
        st.caption(f"⚡ `{descarga.nombre_archivo}` faltaba o estaba desactualizado: se recalculó antes de ofrecerlo.")


def render_panel(necesidad_sel: str) -> None:
    """Panel de la necesidad elegida: título, gráficas, valores clave,
    hallazgos, limitaciones y tablas."""
    carga = cargar(necesidad_sel)
    r = carga.resultado

    st.title(f"{r.id} — {r.titulo}")
    insignia = "🗄️ Desde caché en disco" if carga.desde_cache else "⚡ Recién calculado (no había caché vigente)"
    st.caption(
        f"{insignia} · generado el `{r.fecha_generacion}` (tardó {r.duracion_s:.2f}s en calcularse) · "
        f"esta carga tomó {carga.duracion_carga_s * 1000:.0f}ms."
    )

    if necesidad_sel in servicio.NECESIDADES_ENTREGA:
        render_descarga_json(necesidad_sel)

    col_p, col_d = st.columns(2)
    with col_p:
        st.markdown(f"**Pregunta:** {r.pregunta}")
    with col_d:
        st.markdown(f"**Decisión:** {r.decision}")

    with st.container(border=True):
        st.markdown(f"**Universo:** {r.universo.texto}")
        if r.universo.filtros:
            st.caption("Filtros: " + "; ".join(r.universo.filtros))

    st.subheader("Respuesta")
    for h in r.hallazgos:
        st.markdown(f"- {h}")

    graficas = graficos(r)
    if graficas:
        st.subheader("Gráfica")
    for titulo_grafico, chart in graficas:
        st.markdown(f"*{titulo_grafico}*")
        if chart is None:
            st.info("No hay datos suficientes para esta gráfica con el universo/parámetros actuales.")
        else:
            st.altair_chart(chart, use_container_width=True)

    st.subheader("Valores clave")
    # Las listas (p. ej. el top 10 de nombres) ya están en «Respuesta»: una métrica no las muestra bien.
    escalares = [vc for vc in r.valores_clave if not isinstance(vc.valor, list)]
    n_cols = min(4, len(escalares)) or 1
    cols = st.columns(n_cols)
    for i, vc in enumerate(escalares):
        with cols[i % n_cols]:
            st.metric(vc.etiqueta, _fmt_valor(vc.valor, vc.unidad))

    st.subheader("Consulta SQL de referencia")
    for consulta in r.consultas:
        st.code(consulta.sql, language="sql")

    st.subheader("⚠️ Limitaciones")
    st.caption("Siempre visibles, nunca en un expander colapsado por defecto.")
    for lim in r.limitaciones:
        st.warning(lim, icon="⚠️")

    with st.expander("Parámetros usados en esta necesidad"):
        for p in r.parametros:
            st.markdown(f"**{p.nombre}** = `{p.valor}`  \n{p.justificacion}")

    with st.expander("Resultado crudo de la consulta (la misma tabla que alimenta la gráfica)"):
        for nombre_tabla, filas in r.tablas.items():
            st.caption(f"`{nombre_tabla}` — {r.tablas_criterio_corte.get(nombre_tabla, '')}")
            st.dataframe(_df(filas)[columnas_ordenadas(r.id, filas)] if filas else _df(filas), use_container_width=True, height=min(320, 45 + 30 * len(filas)))


def main() -> None:
    st.set_page_config(page_title="Dashboard Instacart — Fase 3", page_icon="📊", layout="wide")
    inicializar_estado()
    render_panel(render_controles(st.sidebar))


if __name__ == "__main__":
    main()
