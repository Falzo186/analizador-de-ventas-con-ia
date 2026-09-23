"""Dashboard de las 11 necesidades analíticas de Instacart (Fase 3).

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
falta. N10 y N11 miden hasta 6-8s en el peor caso (ver
`docs/FASE2B_REPORTE.md`), muy por encima de lo aceptable en cada rerun de
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
from src.dashboard import graficas_datos as gd
from src.dashboard import servicio
from src.pdf import generador as pdf_generador

NECESIDADES_IDS = [f"N{i}" for i in range(1, 12)]


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
    # conexión y llama a `cargar_necesidad` x11 (barato con caché tibia,
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
# Gráficas por necesidad: cada función recibe el ResultadoNecesidad ya
# cargado y devuelve una lista de (título, gráfica Altair | None) usando
# SOLO las tablas que ya trae `r.tablas` -- ninguna calcula ni inventa datos
# nuevos. `None` significa "sin datos suficientes para esta gráfica" (ver
# `_seguro`); el bucle de render en la sección de UI lo muestra como aviso.
# ---------------------------------------------------------------------------

def graficos_n1(r: ResultadoNecesidad):
    df = gd.datos_n1(r)
    chart = _seguro(df, lambda d: alt.Chart(d).mark_circle(opacity=0.55, size=60).encode(
        x=alt.X("lineas:Q", title="Volumen (líneas, escala log)", scale=alt.Scale(type="log")),
        y=alt.Y("tasa_recompra:Q", title="Tasa de recompra", axis=alt.Axis(format="%")),
        color=alt.Color("clasificacion:N", title="Clasificación"),
        tooltip=["product_name", "lineas", "tasa_recompra", "tasa_abandono", "clasificacion"],
    ).properties(height=420))
    return [("Dispersión: volumen vs. tasa de recompra, por clasificación", chart)]


def graficos_n2(r: ResultadoNecesidad):
    df = gd.datos_n2(r)
    chart = _seguro(df, lambda d: alt.Chart(d).mark_bar().encode(
        x=alt.X("lift:Q", title="Lift"),
        y=alt.Y("par:N", sort="-x", title=None),
        tooltip=["par", "lift", "pedidos_con_par", "confianza_a_b", "confianza_b_a", "soporte"],
    ).properties(height=520))
    return [("Top 20 pares por lift (de los 50 en la tabla)", chart)]


def graficos_n3(r: ResultadoNecesidad):
    df = gd.datos_n3(r)
    chart = _seguro(df, lambda d: alt.Chart(d).mark_rect().encode(
        x=alt.X("order_hour_of_day:O", title="Hora del día (0-23, anónima)"),
        y=alt.Y("order_dow:O", title="Día de la semana (0-6, anónimo)"),
        color=alt.Color("n_pedidos:Q", title="Pedidos", scale=alt.Scale(scheme="blues")),
        tooltip=["order_dow", "order_hour_of_day", "n_pedidos", "indice", "pico", "valle"],
    ).properties(height=320))
    return [("Mapa de calor: pedidos por día x hora", chart)]


def graficos_n4(r: ResultadoNecesidad):
    datos = gd.datos_n4(r)

    def _construir(d):
        hist = alt.Chart(d).mark_bar().encode(
            x=alt.X("razon:Q", bin=alt.Bin(maxbins=20), title="Razón último/típico"),
            y=alt.Y("count():Q", title="Usuarios (dentro del top 50 mostrado)"),
            tooltip=["count()"],
        )
        regla = alt.Chart(pd.DataFrame({"umbral": [datos.umbral_razon]})).mark_rule(
            color="red", strokeDash=[5, 4], size=2
        ).encode(x="umbral:Q")
        return (hist + regla).properties(height=350)

    chart = _seguro(datos.df, _construir)
    return [("Distribución de razón (top 50 por razón) con línea de UMBRAL_RAZON", chart)]


def graficos_n5(r: ResultadoNecesidad):
    df = gd.datos_n5(r)
    chart = _seguro(df, lambda d: alt.Chart(d).mark_bar().encode(
        x=alt.X("indice_ancla:Q", title="Índice ancla"),
        y=alt.Y("product_name:N", sort="-x", title=None),
        tooltip=["product_name", "indice_ancla", "apariciones", "usuarios_distintos", "usuarios_primero", "tasa_primero"],
    ).properties(height=520))
    return [("Top 20 productos por índice_ancla (entre elegibles)", chart)]


def graficos_n6(r: ResultadoNecesidad):
    df = gd.datos_n6(r)
    chart = _seguro(df, lambda d: alt.Chart(d).mark_circle(size=140, opacity=0.75).encode(
        x=alt.X("lineas:Q", title="Volumen (líneas)"),
        y=alt.Y("tasa_recompra:Q", title="Tasa de recompra", axis=alt.Axis(format="%")),
        color=alt.Color("cuadrante:N", title="Cuadrante"),
        tooltip=["nombre", "lineas", "pct_lineas", "tasa_recompra", "cuadrante"],
    ).properties(height=420))
    return [("Departamentos por cuadrante (volumen vs. tasa de recompra)", chart)]


def graficos_n7(r: ResultadoNecesidad):
    datos = gd.datos_n7(r)
    linea = _seguro(datos.df_k, lambda d: alt.Chart(d).mark_line(point=True).encode(
        x=alt.X("k:Q", title="Número de pedido (order_number)"),
        y=alt.Y("tamano_medio:Q", title="Tamaño medio"),
        tooltip=["k", "tamano_medio", "tasa_recompra", "usuarios_n", "productos_nuevos_por_pedido"],
    ).properties(height=320))

    barras = _seguro(datos.df_pareado, lambda d: alt.Chart(d).mark_bar().encode(
        x=alt.X("diferencia_media:Q", title="Diferencia media (pedido 10 − pedido 1)"),
        y=alt.Y("métrica:N", title=None),
        color=alt.condition("datum.diferencia_media >= 0", alt.value("#2a9d8f"), alt.value("#e76f51")),
        tooltip=["métrica", "diferencia_media"],
    ).properties(height=220))
    return [
        ("Tamaño medio por número de pedido (k) — curva de composición, no trayectoria individual", linea),
        (f"Comparación pareada: mismo usuario, pedido 1 vs 10 (n={datos.n_usuarios_pareado:,})", barras),
    ]


def graficos_n8(r: ResultadoNecesidad):
    datos = gd.datos_n8(r)

    def _construir(d):
        hist = alt.Chart(d).mark_bar().encode(
            x=alt.X("bin_decimo:Q", title="índice_novedad (bin de ancho 0.1, 0=nunca repite... 9=siempre lo mismo)"),
            y=alt.Y("n_usuarios:Q", title="Usuarios"),
            tooltip=["bin_decimo", "n_usuarios"],
        )
        if datos.corte_rutinario is not None and datos.corte_explorador is not None:
            reglas = alt.Chart(pd.DataFrame({
                "corte": [datos.corte_rutinario * 10, datos.corte_explorador * 10],
                "tipo": ["rutinario (P25)", "explorador (P75)"],
            })).mark_rule(color="red", strokeDash=[5, 4], size=2).encode(x="corte:Q", tooltip=["tipo", "corte"])
            return (hist + reglas).properties(height=350)
        return hist.properties(height=350)

    chart = _seguro(datos.df, _construir)
    return [("Histograma de índice_novedad, con los cortes rutinario/explorador", chart)]


def graficos_n9(r: ResultadoNecesidad):
    df_hist = gd.datos_n9_histograma(r)
    hist = _seguro(df_hist, lambda d: alt.Chart(d).mark_bar().encode(
        x=alt.X("tamano_bin:Q", title="Tamaño de pedido (último bin = TAM_MAX+ agrupado)"),
        y=alt.Y("pedidos:Q", title="Pedidos"),
        color=alt.condition("datum.es_cubo_final", alt.value("#e76f51"), alt.value("#457b9d")),
        tooltip=["tamano_bin", "pedidos", "es_cubo_final"],
    ).properties(height=320))

    factor_key = st.selectbox(
        "Factor para la desviación intra-usuario", list(gd.FACTORES_N9),
        format_func=lambda k: gd.FACTORES_N9[k], key="n9_factor",
    )
    df_factor = gd.datos_n9_factor(r, factor_key)
    barras = _seguro(df_factor, lambda d: alt.Chart(d).mark_bar().encode(
        x=alt.X("valor:N", title=gd.FACTORES_N9[factor_key], sort=None),
        y=alt.Y("desviacion_intra_usuario:Q", title="Desviación intra-usuario (productos)"),
        color=alt.condition("datum.desviacion_intra_usuario >= 0", alt.value("#2a9d8f"), alt.value("#e76f51")),
        tooltip=["valor", "desviacion_intra_usuario", "tamano_medio_bruto", "pedidos"],
    ).properties(height=320))
    return [
        ("Histograma de tamaño de pedido (naranja = cubo final, >= TAM_MAX+1)", hist),
        (f"Desviación intra-usuario por {gd.FACTORES_N9[factor_key].lower()}", barras),
    ]


def graficos_n10(r: ResultadoNecesidad):
    df = gd.datos_n10(r)
    chart = _seguro(df, lambda d: alt.Chart(d).mark_bar().encode(
        x=alt.X("tramo:O", title="Tramo del carrito (0=inicio ... 4=final)"),
        y=alt.Y("tasa_recompra:Q", title="Tasa de recompra", axis=alt.Axis(format="%")),
        tooltip=["tramo", "lineas", "tasa_recompra"],
    ).properties(height=350))
    return [("Tasa de recompra por tramo (posición relativa en el carrito)", chart)]


def graficos_n11(r: ResultadoNecesidad):
    df = gd.datos_n11(r)
    chart = _seguro(df, lambda d: alt.Chart(d).mark_bar().encode(
        x=alt.X("k:O", title="K"),
        xOffset="metodo:N",
        y=alt.Y("hit_rate:Q", title="Hit-rate", axis=alt.Axis(format="%")),
        color=alt.Color("metodo:N", title="Método"),
        tooltip=["metodo", "k", "hit_rate", "recall_macro", "precision_macro", "n_usuarios"],
    ).properties(height=380))
    return [("Comparación de métodos y K: hit-rate", chart)]


GRAFICOS = {
    "N1": graficos_n1, "N2": graficos_n2, "N3": graficos_n3, "N4": graficos_n4,
    "N5": graficos_n5, "N6": graficos_n6, "N7": graficos_n7, "N8": graficos_n8,
    "N9": graficos_n9, "N10": graficos_n10, "N11": graficos_n11,
}


# ---------------------------------------------------------------------------
# Sidebar: selector de necesidad + botón global de actualización
# ---------------------------------------------------------------------------

def render_controles(area=st.sidebar, *, horizontal: bool = False) -> str:
    """Selector de necesidad, botón "Actualizar datos" y bloque del PDF,
    dibujados en `area` (la barra lateral en standalone, un contenedor del
    panel derecho en la app de dos paneles). Devuelve la necesidad elegida."""
    area.title("📊 Dashboard Instacart")
    area.caption(
        "Fase 3 — 11 necesidades sobre datos reales de Instacart, sin IA. "
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
            barra.progress(contador["i"] / 11, text=f"{estado.id}: {etiqueta} ({estado.duracion_s:.2f}s)")

        resumen = servicio.refrescar_todas(forzar=forzar, al_terminar_cada_una=_avance)
        st.session_state["ultimo_refresco"] = resumen
        st.session_state["version_cache"] += 1  # invalida la caché de sesión de Streamlit (no la de disco)
        _cargar_cacheado.clear()

    ultimo = st.session_state["ultimo_refresco"]
    if ultimo is not None:
        area.success(
            f"Último refresco: {ultimo.n_recalculadas}/11 recalculadas en "
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
        "Recalcular las 11 necesidades antes de generar el PDF",
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

    col_p, col_d = st.columns(2)
    with col_p:
        st.markdown(f"**Pregunta:** {r.pregunta}")
    with col_d:
        st.markdown(f"**Decisión:** {r.decision}")

    with st.container(border=True):
        st.markdown(f"**Universo:** {r.universo.texto}")
        if r.universo.filtros:
            st.caption("Filtros: " + "; ".join(r.universo.filtros))

    st.subheader("Gráficas")
    for titulo_grafico, chart in GRAFICOS[necesidad_sel](r):
        st.markdown(f"*{titulo_grafico}*")
        if chart is None:
            st.info("No hay datos suficientes para esta gráfica con el universo/parámetros actuales.")
        else:
            st.altair_chart(chart, use_container_width=True)

    st.subheader("Valores clave")
    n_cols = min(4, len(r.valores_clave)) or 1
    cols = st.columns(n_cols)
    for i, vc in enumerate(r.valores_clave):
        with cols[i % n_cols]:
            st.metric(vc.etiqueta, _fmt_valor(vc.valor, vc.unidad))

    st.subheader("Hallazgos")
    for h in r.hallazgos:
        st.markdown(f"- {h}")

    st.subheader("⚠️ Limitaciones")
    st.caption("Siempre visibles, nunca en un expander colapsado por defecto.")
    for lim in r.limitaciones:
        st.warning(lim, icon="⚠️")

    with st.expander("Parámetros usados en esta necesidad"):
        for p in r.parametros:
            st.markdown(f"**{p.nombre}** = `{p.valor}`  \n{p.justificacion}")

    with st.expander("Tablas de datos (las mismas que alimentan las gráficas de arriba)"):
        for nombre_tabla, filas in r.tablas.items():
            st.caption(f"`{nombre_tabla}` — {r.tablas_criterio_corte.get(nombre_tabla, '')}")
            st.dataframe(_df(filas), use_container_width=True, height=min(320, 45 + 30 * len(filas)))


def main() -> None:
    st.set_page_config(page_title="Dashboard Instacart — Fase 3", page_icon="📊", layout="wide")
    inicializar_estado()
    render_panel(render_controles(st.sidebar))


if __name__ == "__main__":
    main()
