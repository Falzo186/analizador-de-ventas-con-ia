"""Interfaz web de ventia (Streamlit).

Para correrla:

    .venv\\Scripts\\streamlit run src/ui/app.py

Esta app NO tiene lógica propia de negocio: solo llama a los módulos ya
construidos (`src.llm`, `src.evaluator`). Si algo falla, aquí solo cambia
cómo se *muestra* el error.

Desde la migración a datos reales (ver docs/arquitectura.md, "Migración a
datos reales (Northwind)"), esta UI ya NO simula ventas: los datos son fijos
(Northwind Traders, cargados en data/northwind.db), así que la vieja pestaña
"Punto de venta" (generar venta / generar histórico) no aplica y se quitó.
Solo quedan: preguntarle a la IA sobre los datos reales, y una tabla simple
para explorarlos.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# `streamlit run` solo pone en sys.path la carpeta del script (src/ui/), no la
# raíz del proyecto. La añadimos para poder hacer `import src.<modulo>`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st
from sqlalchemy import inspect, text

from src.db.database import crear_base_de_datos, engine
from src.evaluator.banco import BANCO
from src.evaluator.evaluador import (
    ETIQUETAS_FALLO,
    _fmt_filas,
    construir_reporte_md,
    evaluar_banco,
    resumen_por_categoria,
)
from src.llm.client import MODELO_POR_DEFECTO, LlmNoDisponible, modelos_disponibles
from src.llm.responder import responder_pregunta

st.set_page_config(page_title="ventia", page_icon="🛒", layout="wide")
crear_base_de_datos()

st.title("🛒 ventia")
st.caption("Evaluador de un LLM local sobre los datos reales de Northwind Traders")

tab_ia, tab_explorar = st.tabs(["🤖 Pregúntale a la IA", "🔍 Explorar tablas"])


# ===========================================================================
# Pestaña 1 — Pregúntale a la IA
# ===========================================================================

def _selector_modelo() -> str:
    try:
        modelos = modelos_disponibles()
    except LlmNoDisponible as exc:
        st.warning(f"⚠️ {exc}")
        return st.text_input("Modelo de Ollama", value=MODELO_POR_DEFECTO)

    if not modelos:
        st.warning("Ollama responde pero no tiene modelos descargados. "
                   f"Ejecuta:  ollama pull {MODELO_POR_DEFECTO}")
        return st.text_input("Modelo de Ollama", value=MODELO_POR_DEFECTO)

    idx = modelos.index(MODELO_POR_DEFECTO) if MODELO_POR_DEFECTO in modelos else 0
    return st.selectbox("Modelo de Ollama", modelos, index=idx)


def _mostrar_respuesta(res) -> None:
    # Ollama apagado / modelo ausente: responder_pregunta lo deja en res.error
    if res.sql is None:
        st.error(res.error or "No se pudo generar el SQL.")
        return

    st.markdown("**1 · SQL generado**")
    st.code(res.sql, language="sql")

    st.markdown("**2 · Resultado crudo**")
    if res.filas is not None:
        st.dataframe(
            pd.DataFrame(res.filas, columns=res.columnas),
            use_container_width=True, hide_index=True,
        )
    else:
        st.warning(res.error or "El SQL no se pudo ejecutar.")

    st.markdown("**3 · Respuesta en lenguaje natural**")
    if res.respuesta:
        st.success(res.respuesta)
    else:
        st.warning(res.error or "No se pudo redactar la respuesta.")


def _mostrar_reporte(resultados, modelo: str, segundos: float) -> None:
    aciertos = sum(r.acierto for r in resultados)
    total = len(resultados)
    st.metric("Precisión global", f"{100 * aciertos / total:.1f}%",
              f"{aciertos}/{total} casos")

    tabla = [
        {"Tipo": cat, "Aciertos": ok, "Total": tot,
         "Precisión": f"{100 * ok / tot:.0f}%"}
        for cat, (ok, tot) in resumen_por_categoria(resultados).items()
    ]
    st.dataframe(pd.DataFrame(tabla), use_container_width=True, hide_index=True)

    with st.expander("Ver detalle por caso"):
        detalle = [
            {
                "Pregunta": r.caso.pregunta,
                "Acierto": "✅" if r.acierto else "❌",
                "Motivo": "" if r.acierto
                else ETIQUETAS_FALLO.get(r.motivo_fallo, r.motivo_fallo or ""),
                "Verdad": _fmt_filas(r.verdad_filas),
                "Respuesta del modelo": (r.respuesta_modelo or r.error or "")[:140],
            }
            for r in resultados
        ]
        st.dataframe(pd.DataFrame(detalle), use_container_width=True, hide_index=True)

    try:
        with open("docs/reporte_evaluacion.md", "w", encoding="utf-8") as fh:
            fh.write(construir_reporte_md(resultados, modelo, segundos))
        st.caption("📄 Reporte completo guardado en `docs/reporte_evaluacion.md`")
    except OSError:
        pass


with tab_ia:
    modelo = _selector_modelo()

    pregunta = st.text_input(
        "Tu pregunta sobre Northwind (productos, pedidos, clientes, empleados...)",
        placeholder="¿qué productos están descontinuados actualmente?",
    )
    if st.button("Preguntar", type="primary", disabled=not pregunta.strip()):
        os.environ["VENTIA_LLM_MODELO"] = modelo
        with st.spinner(f"Consultando a {modelo}…"):
            res = responder_pregunta(pregunta)
        _mostrar_respuesta(res)

    st.divider()
    st.subheader("Evaluación completa del modelo")
    st.caption(f"Corre el banco de {len(BANCO)} preguntas de negocio con «{modelo}» y "
               "compara cada respuesta contra su SQL de referencia.")

    if st.button("Correr evaluación completa"):
        os.environ["VENTIA_LLM_MODELO"] = modelo
        barra = st.progress(0.0, text="Evaluando…")

        def _avance(i, total, _r):
            barra.progress(i / total, text=f"Caso {i}/{total}")

        inicio = time.monotonic()
        resultados = evaluar_banco(BANCO, progreso=False, on_caso=_avance)
        barra.empty()
        _mostrar_reporte(resultados, modelo, time.monotonic() - inicio)


# ===========================================================================
# Pestaña 2 — Explorar tablas
# ===========================================================================

# Tablas de negocio de Northwind (se excluyen las de apoyo: Regions,
# Territories, EmployeeTerritories, CustomerDemographics, CustomerCustomerDemo).
_TABLAS_NORTHWIND = [
    "Categories", "Products", "Suppliers", "Customers",
    "Employees", "Shippers", "Orders", "Order Details",
]

# Columnas BLOB (imágenes) que no tiene sentido mostrar en una tabla de texto.
_COLUMNAS_OMITIDAS = {"Photo", "Picture"}


def _columnas_visibles(tabla: str) -> list[str]:
    cols = [c["name"] for c in inspect(engine).get_columns(tabla)]
    return [c for c in cols if c not in _COLUMNAS_OMITIDAS]


with tab_explorar:
    st.subheader("Explorar las tablas de Northwind")
    tabla = st.selectbox("Tabla", _TABLAS_NORTHWIND)
    n_filas = st.slider("Filas a mostrar", 10, 200, 50, step=10)

    with engine.connect() as conn:
        total = conn.execute(text(f'SELECT COUNT(*) FROM "{tabla}"')).scalar_one()
        cols = _columnas_visibles(tabla)
        cols_sql = ", ".join(f'"{c}"' for c in cols)
        filas = conn.execute(
            text(f'SELECT {cols_sql} FROM "{tabla}" LIMIT :n'), {"n": n_filas}
        ).fetchall()

    st.caption(f"{total} filas en total — mostrando las primeras {min(n_filas, total)}.")
    st.dataframe(pd.DataFrame(filas, columns=cols), use_container_width=True, hide_index=True)
