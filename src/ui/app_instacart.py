"""App de Instacart: chat con la IA local y dashboard de las 11
necesidades, en vista alternante estilo Claude.ai (Fase 8).

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
  `render_panel` (Fases 3 y 4, sobre `src.dashboard.servicio`).
- Layout (Fase 8): arriba del área principal hay dos íconos, 💬 y 📊, que
  alternan qué vista ocupa todo el ancho (`st.session_state["vista"]`). La
  barra lateral lista las 11 necesidades como "fijados": un clic abre el
  dashboard con esa necesidad seleccionada, usando la misma clave que el
  selector del dashboard (`necesidad_sel`).
- Cambiar de vista es un rerun completo. Si hay una pregunta en proceso, la
  ejecución en curso se abandona y la pregunta queda en
  `pregunta_en_curso`. Desde el HOTFIX 1, la fase SQL no se repite: se
  reutiliza su resultado, o se espera al hilo que la calcula
  (`_resultado_sql`). Solo la redacción se reanuda al regresar al chat
  (docs/HOTFIX1_REPORTE.md). Ambas vistas avisan que hay una pregunta
  pendiente.
- Entrega del curso (Fase 10): en la vista de dashboard, el expander
  «📋 Entrega» corre el MISMO pipeline del chat con la pregunta fija de
  N1, N2, N4, N9 y N11 y ofrece el resultado como `{Nx}_ia.json`
  (`src.llm.formato_entrega`). No toca el chat normal.
"""

from __future__ import annotations

import dataclasses
import sys
import threading
import time
from pathlib import Path

# `streamlit run` solo agrega al sys.path la carpeta del script (src/ui/).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from src.analytics import registro
from src.llm import client
from src.llm import formato_entrega
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

VISTA_CHAT = "chat"
VISTA_DASHBOARD = "dashboard"
#: Clave de session_state del selector del dashboard (`dashboard.render_controles`).
CLAVE_NECESIDAD = "necesidad_sel"
#: Título corto de cada necesidad: la constante TITULO de su módulo en
#: src/analytics, el mismo texto del título del panel (`r.titulo`).
TITULOS_NECESIDADES = {
    nid: sys.modules[fn.__module__].TITULO for nid, fn in registro.NECESIDADES.items()
}

#: Fase 9: nivel de esfuerzo -> timeout (s) de CADA llamada al modelo
#: (SQL, respuesta). Referencias: medias del benchmark de la Fase 5 (SQL /
#: respuesta: 1.5b 2.9/2.5 s, 8b 12.3/9.6 s, 14b 31.6/25.6 s) y la llamada
#: de SQL medida en esta fase EN FRÍO (recién cargado el modelo: carga +
#: primera evaluación del prompt de ~1,830 tokens) y EN CALIENTE: 1.5b
#: 22.1 s / 1.2-1.6 s, 8b 106.0 s / 4.5-7.1 s, 14b 219.6 s / 7.8 s. Cada
#: nivel cubre un caso con margen (docs/FASE9_REPORTE.md).
#: "Medio" es el valor que ya usaba el chat (TIMEOUT_LLM_*_S = 30 s). NO
#: cambia el timeout de la consulta en DuckDB (10 s), que es una guarda de
#: seguridad y no un parámetro de esfuerzo.
ESFUERZOS: dict[str, tuple[float, float]] = {
    "Bajo": (15.0, 15.0),
    "Medio": (ri.TIMEOUT_LLM_SQL_S, ri.TIMEOUT_LLM_RESPUESTA_S),
    "Alto": (60.0, 60.0),
    "Ultra": (150.0, 150.0),
    "Extremo": (300.0, 300.0),
}
ESFUERZO_DEFAULT = "Medio"
CLAVE_MODELO = "modelo_sel"
CLAVE_ESFUERZO = "esfuerzo_sel"
#: Configuración (modelo + esfuerzo) congelada al ENVIAR la pregunta en curso.
CLAVE_CONFIG_EN_CURSO = "config_en_curso"
AVISO_OLLAMA_CAIDO = (
    "Ollama no responde. Debe estar corriendo antes de usar el chat; verifica con "
    "`systemctl status ollama`. La app no puede iniciarlo ni detenerlo: el servicio es del "
    "sistema y requiere contraseña de administrador (docs/FASE9_INVESTIGACION.md)."
)


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
    if "vista" not in st.session_state:
        st.session_state["vista"] = VISTA_CHAT
    if CLAVE_ESFUERZO not in st.session_state:
        st.session_state[CLAVE_ESFUERZO] = ESFUERZO_DEFAULT
    if CLAVE_CONFIG_EN_CURSO not in st.session_state:
        st.session_state[CLAVE_CONFIG_EN_CURSO] = None
    if CLAVE_NECESIDAD in st.session_state:
        # Streamlit borra el estado de un widget con `key` en toda ejecución
        # en la que no se dibuja (aquí, la vista de chat). Reasignarlo lo
        # conserva: al volver al dashboard sigue la necesidad elegida.
        st.session_state[CLAVE_NECESIDAD] = st.session_state[CLAVE_NECESIDAD]


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


def config_actual() -> dict:
    """Modelo y esfuerzo elegidos AHORA en la UI, con sus timeouts."""
    esfuerzo = st.session_state.get(CLAVE_ESFUERZO, ESFUERZO_DEFAULT)
    timeout_sql, timeout_resp = ESFUERZOS[esfuerzo]
    return {
        "modelo": st.session_state.get(CLAVE_MODELO) or client.modelo_actual_instacart(),
        "esfuerzo": esfuerzo, "timeout_sql_s": timeout_sql, "timeout_respuesta_s": timeout_resp,
    }


def mensaje_asistente(res: ri.ResultadoInstacart, contexto: list[tuple[str, str]], config: dict | None = None) -> dict:
    config = config or config_actual()
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
            "modelo": config["modelo"],
            "esfuerzo": config["esfuerzo"],
            "timeouts_s": (config["timeout_sql_s"], config["timeout_respuesta_s"]),
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

#: Resultado de la fase SQL de la pregunta en curso:
#: {"pregunta", "hilo", "res"}. Ver `_resultado_sql`.
CLAVE_SQL_EN_CURSO = "sql_en_curso"
ESPERA_POLL_S = 0.2


def _resultado_sql(pregunta: str, contexto: list[tuple[str, str]], config: dict) -> ri.ResultadoInstacart:
    """Fase SQL (`responder_pregunta_instacart(redactar=False)`) sin repetir
    trabajo si un cambio de vista abandona la ejecución (HOTFIX 1).

    Un cambio de vista es un rerun completo. Con `runner.fastReruns`,
    Streamlit arranca la ejecución nueva en otro hilo sin esperar a la
    anterior, y la anterior solo se detiene en su siguiente llamada a
    Streamlit. La fase SQL no tiene ninguna, así que el hilo abandonado la
    completa igual (2+ llamadas a Ollama) y antes del HOTFIX descartaba el
    resultado, mientras cada regreso al chat lanzaba otro cálculo completo.
    Ahora el resultado se guarda en session_state en cuanto termina, incluso
    desde un hilo abandonado, y:
      - si ya está calculado para esta pregunta, se reutiliza;
      - si otro hilo todavía lo calcula, se espera a ese hilo;
      - solo si nadie lo tiene (o el hilo murió sin resultado) se calcula.

    Fase 9: lo mismo vale si a mitad de la pregunta cambian el modelo o el
    esfuerzo en la UI (también es un rerun completo). `config` es la
    configuración congelada al ENVIAR la pregunta (`CLAVE_CONFIG_EN_CURSO`),
    así que un cambio posterior no crea un cálculo nuevo. El modelo forma
    parte de la clave solo para no reutilizar el resultado de otro modelo si
    la MISMA pregunta se vuelve a enviar explícitamente con otro modelo.
    """
    en_curso = st.session_state.get(CLAVE_SQL_EN_CURSO)
    if (en_curso is not None and en_curso["pregunta"] == pregunta
            and en_curso.get("modelo", config["modelo"]) == config["modelo"]):
        while en_curso["res"] is None and en_curso["hilo"].is_alive():
            time.sleep(ESPERA_POLL_S)
        if en_curso["res"] is not None:
            # Copia: el hilo abandonado no debe compartir el objeto que la
            # redacción de esta ejecución va a completar.
            return dataclasses.replace(en_curso["res"])
    en_curso = {"pregunta": pregunta, "modelo": config["modelo"], "hilo": threading.current_thread(), "res": None}
    st.session_state[CLAVE_SQL_EN_CURSO] = en_curso
    en_curso["res"] = ri.responder_pregunta_instacart(
        pregunta, db_path=ri.DB_PATH_DEFAULT, redactar=False, contexto=contexto,
        modelo=config["modelo"], timeout_sql_llm_s=config["timeout_sql_s"],
        timeout_respuesta_llm_s=config["timeout_respuesta_s"],
    )
    return dataclasses.replace(en_curso["res"])


def responder_en_vivo(pregunta: str) -> None:
    historial = st.session_state["historial"]
    contexto = contexto_de_seguimiento(historial)
    config = st.session_state.get(CLAVE_CONFIG_EN_CURSO) or config_actual()
    with st.chat_message("user"):
        st.markdown(pregunta)
    with st.chat_message("assistant"):
        with st.status("Generando y ejecutando la consulta…", expanded=False) as estado:
            res = _resultado_sql(pregunta, contexto, config)
            estado.update(label="Consulta lista" if res.ok else "La consulta no se pudo completar",
                          state="complete" if res.ok else "error")
        if res.ok:
            st.write_stream(ri.redaccion_en_stream(
                res, modelo=config["modelo"], timeout_s=config["timeout_respuesta_s"],
            ))
        mensaje = mensaje_asistente(res, contexto, config)
        if res.ok and res.origen_respuesta != "modelo":
            # La cifra alterada ya se vio en el stream: se avisa y se muestra la corregida.
            render_aviso_cifras(mensaje["metadatos"])
            st.markdown(f"**Respuesta corregida:** {res.respuesta}")
        elif not res.ok:
            render_error(mensaje["metadatos"], mensaje["contenido"])
        render_detalles(mensaje)
    historial.append({"rol": ROL_USUARIO, "contenido": pregunta, "sql": None, "metadatos": {}})
    historial.append(mensaje)
    st.session_state[CLAVE_SQL_EN_CURSO] = None
    st.session_state[CLAVE_CONFIG_EN_CURSO] = None


def render_chat() -> None:
    st.subheader("💬 Pregúntale a la IA (Instacart)")
    st.caption(
        f"Modelo local: `{config_actual()['modelo']}` · esfuerzo {config_actual()['esfuerzo']} "
        "(⚙️ arriba para cambiarlo). Cada respuesta sale de una consulta SQL "
        "de solo lectura sobre los datos reales; el SQL queda visible en «Detalles»."
    )
    for mensaje in st.session_state["historial"]:
        render_mensaje(mensaje)

    st.chat_input(
        "Escribe tu pregunta sobre los pedidos de Instacart…", key="chat_input", on_submit=_al_enviar_pregunta,
    )
    pregunta = st.session_state["pregunta_en_curso"]
    if pregunta:
        responder_en_vivo(pregunta)
        st.session_state["pregunta_en_curso"] = None


def _al_enviar_pregunta() -> None:
    # Callback: corre ANTES de la ejecución, así la barra superior ya sabe
    # que hay una pregunta en proceso cuando se dibuja.
    st.session_state["pregunta_en_curso"] = st.session_state["chat_input"]
    # Fase 9: la pregunta se responde con el modelo/esfuerzo de ESTE momento;
    # cambiarlos después aplica a la siguiente pregunta, no a esta.
    st.session_state[CLAVE_CONFIG_EN_CURSO] = config_actual()


# ---------------------------------------------------------------------------
# Dashboard (reusado tal cual de src/ui/dashboard.py)
# ---------------------------------------------------------------------------

def render_dashboard() -> None:
    with st.container(border=True):
        necesidad = dashboard.render_controles(st.container(), horizontal=True)
    render_entrega_ia()
    dashboard.render_panel(necesidad)


# ---------------------------------------------------------------------------
# Fase 10 (entregable de curso): JSON de la IA local por necesidad
# ---------------------------------------------------------------------------

#: {necesidad: formato_entrega.EntregaIA} de la última generación de cada una.
CLAVE_ENTREGAS_IA = "entregas_ia"
AVISO_ENTREGA_IA = (
    "Esta salida es la respuesta **libre** de la IA local a la pregunta fija de cada necesidad: pasa por "
    "las mismas guardas del chat (SQL de solo lectura, timeouts, validación de cifras), pero puede tener "
    "errores semánticos: un SQL válido que no responde lo que se pregunta (ver README). La referencia "
    "determinista es el JSON del software («⬇️ Descargar JSON de esta necesidad» en cada panel)."
)


def render_entrega_ia() -> None:
    entregas = st.session_state.setdefault(CLAVE_ENTREGAS_IA, {})
    en_curso = bool(st.session_state.get("pregunta_en_curso"))
    with st.expander("📋 Entrega: JSON de la IA para las 5 necesidades", expanded=bool(entregas)):
        st.info(AVISO_ENTREGA_IA, icon="ℹ️")
        config = config_actual()
        st.caption(f"Modelo: `{config['modelo']}` · esfuerzo {config['esfuerzo']} (⚙️ arriba para cambiarlo).")
        for nid in formato_entrega.NECESIDADES_ENTREGA:
            st.markdown(f"**{nid} · {TITULOS_NECESIDADES[nid]}** — {formato_entrega.PREGUNTAS_ENTREGA[nid]}")
            if st.button(f"Generar JSON de IA para {nid}", key=f"generar_ia_{nid}", disabled=en_curso,
                         help="Deshabilitado mientras el chat responde una pregunta." if en_curso else None):
                with st.spinner(f"La IA responde la pregunta de {nid}…"):
                    entregas[nid] = formato_entrega.generar_entrega_ia(
                        nid, modelo=config["modelo"], timeout_sql_llm_s=config["timeout_sql_s"],
                        timeout_respuesta_llm_s=config["timeout_respuesta_s"],
                    )
            entrega = entregas.get(nid)
            if entrega is None:
                continue
            if not entrega.ok:
                render_error({"tipo_error": tipo_de_error(entrega.res)}, entrega.res.error)
                if entrega.res.sql:
                    st.code(entrega.res.sql, language="sql")
                continue
            if entrega.res.origen_respuesta != "modelo":
                render_aviso_cifras({"origen_respuesta": entrega.res.origen_respuesta,
                                     "cifras_rechazadas": entrega.res.cifras_rechazadas})
            st.json(entrega.documento, expanded=False)
            st.download_button(
                f"⬇️ Descargar {formato_entrega.nombre_archivo(nid)}",
                data=formato_entrega.a_json(entrega.documento).encode("utf-8"),
                file_name=formato_entrega.nombre_archivo(nid), mime="application/json",
                key=f"descargar_ia_{nid}",
            )


# ---------------------------------------------------------------------------
# Layout: barra de íconos + vista activa a todo el ancho + fijados en la sidebar
# ---------------------------------------------------------------------------

def _ir_a(vista: str) -> None:
    st.session_state["vista"] = vista


def _abrir_necesidad(nid: str) -> None:
    st.session_state["vista"] = VISTA_DASHBOARD
    st.session_state[CLAVE_NECESIDAD] = nid


def render_barra_vistas():
    """Íconos 💬/📊 y, si hay una pregunta en proceso, el aviso correspondiente.
    Devuelve el contenedor del aviso de la vista de chat, para vaciarlo cuando
    la respuesta termina (si no, "Respondiendo…" quedaría en pantalla)."""
    vista = st.session_state["vista"]
    c_chat, c_dash, c_config, _resto = st.columns([1, 1, 1, 17], gap="small")
    c_chat.button(
        "💬", key="vista_chat", help="Chat", on_click=_ir_a, args=(VISTA_CHAT,),
        type="primary" if vista == VISTA_CHAT else "secondary", use_container_width=True,
    )
    c_dash.button(
        "📊", key="vista_dashboard", help="Dashboard", on_click=_ir_a, args=(VISTA_DASHBOARD,),
        type="primary" if vista == VISTA_DASHBOARD else "secondary", use_container_width=True,
    )
    with c_config.popover("⚙️", help="Modelo, esfuerzo y memoria de Ollama", use_container_width=True):
        render_configuracion()
    pendiente = st.session_state["pregunta_en_curso"]
    aviso = st.empty()
    if pendiente and vista == VISTA_DASHBOARD:
        aviso.info(
            f"⏳ Hay una pregunta pendiente en el chat: «{pendiente}». Se procesa en la vista de chat: "
            "vuelve a 💬 para verla (la consulta sigue en proceso; la redacción se retoma al volver).",
        )
    elif pendiente:
        aviso.caption("⏳ Respondiendo… si cambias de vista, la consulta sigue y la respuesta se retoma al regresar.")
    return aviso


def _liberar_modelos(nombres: list[str]) -> None:
    liberados, errores = [], []
    for nombre in nombres:
        try:
            client.liberar_modelo(nombre)
            liberados.append(nombre)
        except client.LlmNoDisponible as exc:
            errores.append(str(exc))
    st.session_state["resultado_liberar"] = (liberados, errores)


def render_configuracion() -> None:
    """Contenido del popover ⚙️ (Fase 9). Todo lo que muestra sale de la API
    de Ollama en esta ejecución, nada es supuesto."""
    st.markdown("**Modelo**")
    try:
        modelos = client.modelos_disponibles()
    except client.LlmNoDisponible:
        modelos = None
    if modelos is None:
        st.error(AVISO_OLLAMA_CAIDO, icon="🔌")
    elif not modelos:
        st.warning("Ollama responde, pero no tiene modelos descargados (`ollama pull <modelo>`).")
    else:
        if st.session_state.get(CLAVE_MODELO) not in modelos:
            preferido = client.modelo_actual_instacart()
            st.session_state[CLAVE_MODELO] = preferido if preferido in modelos else modelos[0]
        st.selectbox("Modelo de Ollama", modelos, key=CLAVE_MODELO,
                     help="Solo los modelos que Ollama tiene descargados ahora (`ollama list`). "
                          "Se usa desde la siguiente pregunta.")

    st.select_slider("Esfuerzo", options=list(ESFUERZOS), key=CLAVE_ESFUERZO,
                     help="Tiempo máximo que se espera cada llamada al modelo. La PRIMERA pregunta "
                          "después de cargar un modelo tarda mucho más (medido aquí: 1.5b ~22 s, "
                          "8b ~106 s, 14b ~220 s); las siguientes, segundos. Bajo: 1.5b ya cargado · "
                          "Medio: 1.5b siempre, 8b cargado · Alto: 8b/14b cargados · Ultra: 8b recién "
                          "cargado · Extremo: 14b recién cargado.")
    t_sql, t_resp = ESFUERZOS[st.session_state[CLAVE_ESFUERZO]]
    st.caption(f"Timeout por llamada: {t_sql:.0f} s (SQL) · {t_resp:.0f} s (respuesta). "
               "El timeout de la consulta en DuckDB (10 s) es una guarda y no cambia.")

    st.divider()
    st.markdown("**Memoria de Ollama**")
    cargados = None
    if modelos is not None:
        try:
            cargados = client.modelos_cargados()
        except client.LlmNoDisponible:
            cargados = None
    if cargados is None:
        st.caption("Estado desconocido: Ollama no responde.")
    elif not cargados:
        st.caption("Ningún modelo cargado en memoria (`/api/ps` vacío).")
    else:
        for m in cargados:
            mib = (m["size_bytes"] or 0) / 2**20
            st.caption(f"🟢 `{m['nombre']}` cargado · {mib:,.0f} MiB en memoria (según `/api/ps`)")
    en_curso = bool(st.session_state.get("pregunta_en_curso"))
    st.button(
        "🧹 Liberar modelo de memoria", key="liberar_memoria", use_container_width=True,
        disabled=not cargados or en_curso, on_click=_liberar_modelos,
        args=([m["nombre"] for m in cargados or []],),
        help="Descarga de la RAM los modelos cargados (keep_alive: 0), sin tocar el servicio. "
             "La siguiente pregunta pagará la carga en frío (1.5b ~22 s). "
             + ("Deshabilitado mientras se responde una pregunta." if en_curso else ""),
    )
    resultado = st.session_state.pop("resultado_liberar", None)
    if resultado:
        liberados, errores = resultado
        if liberados:
            st.success(f"Liberado: {', '.join(liberados)}. El estado de arriba es el de `/api/ps` después de liberar.")
        for e in errores:
            st.error(e)


def render_fijados() -> None:
    st.sidebar.markdown("**📌 Necesidades fijadas**")
    en_dashboard = st.session_state["vista"] == VISTA_DASHBOARD
    seleccionada = st.session_state.get(CLAVE_NECESIDAD, dashboard.NECESIDADES_IDS[0])
    for nid in dashboard.NECESIDADES_IDS:
        st.sidebar.button(
            f"{nid} · {TITULOS_NECESIDADES[nid]}", key=f"fijado_{nid}",
            on_click=_abrir_necesidad, args=(nid,), use_container_width=True,
            type="primary" if en_dashboard and nid == seleccionada else "secondary",
        )


_CSS_FIJADOS = """
<style>
section[data-testid="stSidebar"] div.stButton > button { justify-content: flex-start; text-align: left; }
</style>
"""


def main() -> None:
    st.set_page_config(page_title="Instacart — chat y dashboard", page_icon="🛒", layout="wide")
    inicializar_estado()
    st.markdown(_CSS_FIJADOS, unsafe_allow_html=True)
    render_fijados()
    aviso = render_barra_vistas()
    if st.session_state["vista"] == VISTA_DASHBOARD:
        render_dashboard()
    else:
        render_chat()
        aviso.empty()


if __name__ == "__main__":
    main()
