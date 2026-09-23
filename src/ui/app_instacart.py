"""App de dos paneles de Instacart (Fase 6): chat con la IA local a la
izquierda y dashboard de las 11 necesidades a la derecha.

Para correrla:

    streamlit run src/ui/app_instacart.py

Solo usa la ruta Instacart (`src.llm.responder_instacart`). No importa nada
de la ruta Northwind (`src/ui/app.py`, `src.llm.responder`).

- Chat: `st.session_state["historial"]` guarda la conversación como
  `{rol, contenido, sql, metadatos}`. Cada pregunta pasa por
  `responder_pregunta_instacart(..., redactar=False)` (SQL + guardas +
  reintento) y la redacción llega en streaming con
  `st.write_stream(redaccion_en_stream(res))`. La UI no rehace el flujo:
  solo muestra el `ResultadoInstacart`.
- Contexto: al modelo solo se le mandan la pregunta actual y los últimos
  `N_TURNOS_CONTEXTO` turnos exitosos como (pregunta, SQL), nunca las filas
  ni el historial completo. Ver docs/FASE6_REPORTE.md.
- Dashboard: se reusan tal cual `src.ui.dashboard.render_controles` y
  `render_panel` (Fases 3 y 4, sobre `src.dashboard.servicio`), dentro de un
  `st.fragment`. En Streamlit 1.39, el rerun de un fragmento no interrumpe
  una ejecución completa en curso: queda en cola. Tocar el dashboard
  mientras el chat genera no corta la respuesta, pero el dashboard se
  actualiza cuando el chat termina (no es concurrente).
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run` solo agrega al sys.path la carpeta del script (src/ui/).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from src.llm import client
from src.llm import responder_instacart as ri
from src.ui import dashboard

#: Turnos previos (pregunta + SQL) que se mandan como contexto de
#: seguimiento. El prompt de SQL ya ocupa ~1,742 tokens de los 4,096 con
#: los que Ollama carga qwen2.5-coder:1.5b; cada turno previo ocupa ~40-80.
N_TURNOS_CONTEXTO = 3
#: Filas del resultado que se guardan en el historial para mostrarlas.
MAX_FILAS_HISTORIAL = 25

ROL_USUARIO = "usuario"
ROL_ASISTENTE = "asistente"


# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------

def inicializar_estado() -> None:
    dashboard.inicializar_estado()
    if "historial" not in st.session_state:
        st.session_state["historial"] = []
    if "pregunta_en_curso" not in st.session_state:
        # Pregunta enviada cuya respuesta no terminó (p. ej. un rerun completo
        # la interrumpió). Se retoma en la siguiente ejecución, no se pierde.
        st.session_state["pregunta_en_curso"] = None


def contexto_de_seguimiento(historial: list[dict], n: int = N_TURNOS_CONTEXTO) -> list[tuple[str, str]]:
    """Últimos `n` turnos del asistente que sí ejecutaron SQL, como (pregunta, SQL)."""
    turnos = [
        (m["metadatos"]["pregunta"], m["sql"])
        for m in historial
        if m["rol"] == ROL_ASISTENTE and m["metadatos"].get("filas") is not None and m["sql"]
    ]
    return turnos[-n:] if n > 0 else []


def tipo_de_error(res: ri.ResultadoInstacart) -> str | None:
    """Clasifica el error de `res` para mostrarlo (no cambia el texto)."""
    if res.ok:
        return None
    if res.error.startswith("No puedo ejecutar esa consulta"):
        return "rechazo"
    if "tardó demasiado" in res.error:
        return "timeout"
    return "error"


def mensaje_asistente(res: ri.ResultadoInstacart, contexto: list[tuple[str, str]]) -> dict:
    return {
        "rol": ROL_ASISTENTE,
        "contenido": res.respuesta if res.ok else res.error,
        "sql": res.sql,
        "metadatos": {
            "pregunta": res.pregunta,
            "ok": res.ok,
            "tipo_error": tipo_de_error(res),
            "intentos": res.intentos,
            "motivo_reintento": res.motivo_reintento,
            "origen_respuesta": res.origen_respuesta,
            "cifras_rechazadas": list(res.cifras_rechazadas),
            "columnas": res.columnas,
            "filas": None if res.filas is None else [list(f) for f in res.filas[:MAX_FILAS_HISTORIAL]],
            "n_filas": None if res.filas is None else len(res.filas),
            "contexto_usado": len(contexto),
            "modelo": client.modelo_actual_instacart(),
        },
    }


# ---------------------------------------------------------------------------
# Render de un turno (igual para uno en vivo que para uno del historial)
# ---------------------------------------------------------------------------

_ICONOS_ERROR = {"rechazo": "🛡️", "timeout": "⏱️", "error": "⚠️"}
_TITULOS_ERROR = {
    "rechazo": "Consulta rechazada por las guardas de seguridad",
    "timeout": "Se canceló por tiempo",
    "error": "No se pudo responder",
}


def render_error(meta: dict, contenido: str) -> None:
    tipo = meta.get("tipo_error") or "error"
    st.error(f"**{_TITULOS_ERROR[tipo]}.** {contenido}", icon=_ICONOS_ERROR[tipo])


def render_aviso_cifras(meta: dict) -> None:
    if meta.get("origen_respuesta") in ("reintento", "plantilla"):
        origen = "una segunda redacción" if meta["origen_respuesta"] == "reintento" else "una plantilla con el valor real"
        st.warning(
            f"La redacción del modelo alteró la cifra {', '.join(meta['cifras_rechazadas'])} "
            f"(no está en el resultado); se reemplazó por {origen}.",
            icon="🔢",
        )


def render_detalles(mensaje: dict) -> None:
    meta = mensaje["metadatos"]
    with st.expander("Detalles: SQL, intentos y datos", expanded=False):
        if mensaje["sql"]:
            st.code(mensaje["sql"], language="sql")
        else:
            st.caption("No se llegó a generar SQL.")
        if meta.get("motivo_reintento"):
            st.caption(f"Reintento: el primer SQL se rechazó por: {meta['motivo_reintento']}")
        st.caption(
            f"Intentos de SQL: {meta.get('intentos', 0)} · contexto: {meta.get('contexto_usado', 0)} "
            f"turno(s) previo(s) · modelo: {meta.get('modelo')}"
            + (f" · redacción: {meta['origen_respuesta']}" if meta.get("origen_respuesta") else "")
        )
        if meta.get("filas") is not None:
            st.dataframe(pd.DataFrame(meta["filas"], columns=meta["columnas"]), use_container_width=True)
            if meta["n_filas"] > MAX_FILAS_HISTORIAL:
                st.caption(f"Se muestran {MAX_FILAS_HISTORIAL} de {meta['n_filas']:,} filas.")


def render_mensaje(mensaje: dict) -> None:
    if mensaje["rol"] == ROL_USUARIO:
        with st.chat_message("user"):
            st.markdown(mensaje["contenido"])
        return
    meta = mensaje["metadatos"]
    with st.chat_message("assistant"):
        if meta["ok"]:
            render_aviso_cifras(meta)
            st.markdown(mensaje["contenido"])
        else:
            render_error(meta, mensaje["contenido"])
        render_detalles(mensaje)


# ---------------------------------------------------------------------------
# Un turno en vivo
# ---------------------------------------------------------------------------

def responder_en_vivo(pregunta: str) -> None:
    historial = st.session_state["historial"]
    contexto = contexto_de_seguimiento(historial)
    with st.chat_message("user"):
        st.markdown(pregunta)
    with st.chat_message("assistant"):
        with st.status("Generando y ejecutando la consulta…", expanded=False) as estado:
            res = ri.responder_pregunta_instacart(
                pregunta, db_path=ri.DB_PATH_DEFAULT, redactar=False, contexto=contexto,
            )
            estado.update(label="Consulta lista" if res.ok else "La consulta no se pudo completar",
                          state="complete" if res.ok else "error")
        if res.ok:
            st.write_stream(ri.redaccion_en_stream(res))
        mensaje = mensaje_asistente(res, contexto)
        if res.ok and res.origen_respuesta != "modelo":
            # La cifra alterada ya se vio en el stream: se avisa y se muestra la corregida.
            render_aviso_cifras(mensaje["metadatos"])
            st.markdown(f"**Respuesta corregida:** {res.respuesta}")
        elif not res.ok:
            render_error(mensaje["metadatos"], mensaje["contenido"])
        render_detalles(mensaje)
    historial.append({"rol": ROL_USUARIO, "contenido": pregunta, "sql": None, "metadatos": {}})
    historial.append(mensaje)


def render_chat() -> None:
    st.subheader("💬 Pregúntale a la IA (Instacart)")
    st.caption(
        f"Modelo local: `{client.modelo_actual_instacart()}`. Cada respuesta sale de una consulta SQL "
        "de solo lectura sobre los datos reales; el SQL queda visible en «Detalles»."
    )
    for mensaje in st.session_state["historial"]:
        render_mensaje(mensaje)

    nueva = st.chat_input("Escribe tu pregunta sobre los pedidos de Instacart…", key="chat_input")
    if nueva:
        st.session_state["pregunta_en_curso"] = nueva
    pregunta = st.session_state["pregunta_en_curso"]
    if pregunta:
        responder_en_vivo(pregunta)
        st.session_state["pregunta_en_curso"] = None


# ---------------------------------------------------------------------------
# Dashboard (reusado tal cual de src/ui/dashboard.py)
# ---------------------------------------------------------------------------

@st.fragment
def render_dashboard() -> None:
    with st.container(border=True):
        necesidad = dashboard.render_controles(st.container(), horizontal=True)
    dashboard.render_panel(necesidad)


def main() -> None:
    st.set_page_config(page_title="Instacart — chat y dashboard", page_icon="🛒", layout="wide")
    inicializar_estado()
    col_chat, col_dashboard = st.columns([5, 6], gap="large")
    # El dashboard se dibuja ANTES de procesar la pregunta del chat: así ya
    # está en pantalla mientras el chat genera su respuesta.
    with col_dashboard:
        render_dashboard()
    with col_chat:
        render_chat()


if __name__ == "__main__":
    main()
