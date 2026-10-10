"""App de Instacart: chat con la IA local y dashboard de las 10
necesidades (Fase 24), en vista alternante estilo Claude.ai (Fase 8).

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
  barra lateral lista las 10 necesidades como "fijados": un clic abre el
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
  cada una de las 10 necesidades y ofrece el resultado como `{Nx}_ia.json`
  (`src.llm.formato_entrega`). No toca el chat normal.
- Iteraciones con juez IA (Fase 11): en ⚙️, «Cantidad de iteraciones»
  (default 5) y los modelos pequeño y grande; en la vista de dashboard, el
  expander «🧪 Iteraciones con juez IA» corre N veces la pregunta de la
  necesidad seleccionada con cada modelo (temperatura 0.5) y muestra el
  veredicto del juez (temperatura 0.0) de cada intento contra la respuesta
  de referencia (`src.llm.iteraciones_juez`). El chat sigue en 0.0.
- Panel de iteraciones (Fase 23): pregunta y respuesta de referencia arriba;
  una tarjeta por intento, "Intento k de N · modelo", con su SQL (y el
  rechazado, si hubo reintento), resultado crudo, redacción final y de dónde
  salió (modelo/reintento/plantilla, con la primera redacción descartada) y
  el veredicto del juez. Modo «Autocorrección encadenada» (default) o
  «Independientes» en ⚙️. La corrida se guarda antes de empezar y cada
  intento apenas termina: ninguno se pierde si la ejecución se interrumpe.
  Export del log (.txt legible y .json) por necesidad o de todas.
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
from src.llm import bitacora_chat
from src.llm import iteraciones_juez as ij
from src.llm import responder_instacart as ri
from src.ui import dashboard, estilos

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
#: Título corto de cada necesidad: el `titulo` de su definición en
#: src.analytics.necesidades, el mismo texto del título del panel (`r.titulo`).
TITULOS_NECESIDADES = {nid: registro.definicion(nid).titulo for nid in registro.NECESIDADES}

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
#: Fase 27: revisar la sentencia SQL del chat con el revisor (una llamada más al modelo).
CLAVE_REVISAR_SQL = "revisar_sql_sel"
#: Fase 11: configuración del modo de iteraciones con juez (popover ⚙️).
CLAVE_ITERACIONES = "iteraciones_sel"
CLAVE_MODELO_PEQUENO = "modelo_pequeno_sel"
CLAVE_MODELO_GRANDE = "modelo_grande_sel"
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
    if CLAVE_ITERACIONES not in st.session_state:
        st.session_state[CLAVE_ITERACIONES] = ij.ITERACIONES_DEFAULT
    if CLAVE_MODO_ITERACION not in st.session_state:
        st.session_state[CLAVE_MODO_ITERACION] = ij.MODO_DEFAULT
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
        "revisar_sql": bool(st.session_state.get(CLAVE_REVISAR_SQL, True)),
    }


def config_iteraciones() -> dict:
    """Configuración del modo de iteraciones (Fase 11): N, modelo pequeño y
    grande, y los timeouts del esfuerzo elegido (el juez usa el de respuesta)."""
    base = config_actual()
    return {
        "iteraciones": int(st.session_state.get(CLAVE_ITERACIONES, ij.ITERACIONES_DEFAULT)),
        "modelo_pequeno": st.session_state.get(CLAVE_MODELO_PEQUENO) or ij.MODELO_PEQUENO_DEFAULT,
        "modelo_grande": st.session_state.get(CLAVE_MODELO_GRANDE) or ij.MODELO_GRANDE_DEFAULT,
        "modo": st.session_state.get(CLAVE_MODO_ITERACION) or ij.MODO_DEFAULT,
        "esfuerzo": base["esfuerzo"],
        "timeout_sql_s": base["timeout_sql_s"],
        "timeout_respuesta_s": base["timeout_respuesta_s"],
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
            "revision_sql": res.revision_sql,
            "reintento_por_revision": res.reintento_por_revision,
            "lecciones_nuevas": list(res.lecciones_nuevas),
            "lecciones_en_prompt": res.lecciones_en_prompt,
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


def _texto_y_bloque(contenido: str) -> str:
    """Primera línea como texto y, si el mensaje sigue (p. ej. «Candidate
    bindings» y «LINE 3: …» de DuckDB), el resto en un bloque de código: así
    se lee como un solo mensaje y no como párrafos sueltos (Fase 27, UX)."""
    primera, _, resto = (contenido or "").strip().partition("\n")
    return primera + (f"\n\n```text\n{resto.strip()}\n```" if resto.strip() else "")


def render_error(meta: dict, contenido: str) -> None:
    tipo = meta.get("tipo_error") or "error"
    st.error(f"**{_TITULOS_ERROR[tipo]}.** {_texto_y_bloque(contenido)}", icon=_ICONOS_ERROR[tipo])


def render_aviso_cifras(meta: dict) -> None:
    if meta.get("origen_respuesta") in ("reintento", "plantilla"):
        origen = "una segunda redacción" if meta["origen_respuesta"] == "reintento" else "una plantilla con el valor real"
        st.warning(
            f"La redacción del modelo alteró la cifra {', '.join(meta['cifras_rechazadas'])} "
            f"(no está en el resultado); se reemplazó por {origen}.",
            icon="🔢",
        )


def render_aprendizaje(meta: dict) -> None:
    """Fase 27: revisión de la sentencia SQL y bitácora de esta pregunta."""
    rev = meta.get("revision_sql")
    if rev:
        if rev.get("sospechoso"):
            desenlace = ("se reintentó con ese motivo" if meta.get("reintento_por_revision") else
                         "el reintento no ejecutó; se conservó el resultado original" if rev.get("reintento_error") else
                         "no quedaban reintentos")
            st.caption(f"🧑‍⚖️ Revisión de la sentencia: ⚠️ sospechosa — {rev.get('motivo')} ({desenlace}).")
        else:
            st.caption("🧑‍⚖️ Revisión de la sentencia: sin problemas señalados.")
    if meta.get("lecciones_en_prompt"):
        st.caption(f"📓 El prompt de esta pregunta llevaba {meta['lecciones_en_prompt']} lección(es) de la bitácora.")
    for leccion in meta.get("lecciones_nuevas") or []:
        st.caption(f"📓 Lección agregada a la bitácora: {leccion}")


def render_detalles(mensaje: dict) -> None:
    """Fase 27 (UX): el detalle técnico, colapsado y con secciones rotuladas
    (SQL · intentos · revisión y bitácora · datos), en vez de captions seguidas."""
    meta = mensaje["metadatos"]
    with st.expander("🔎 Detalles técnicos: SQL, intentos y datos", expanded=False):
        st.markdown("**SQL ejecutado**")
        if mensaje["sql"]:
            st.code(mensaje["sql"], language="sql")
        else:
            st.caption("No se llegó a generar SQL.")
        if meta.get("motivo_reintento"):
            st.markdown("**Primer intento rechazado**")
            st.caption("Reintento: el primer SQL se rechazó por:")
            st.code(meta["motivo_reintento"], language="text")
        if meta.get("revision_sql") or meta.get("lecciones_en_prompt") or meta.get("lecciones_nuevas"):
            st.markdown("**Revisión y bitácora**")
        render_aprendizaje(meta)
        st.caption(
            f"Intentos de SQL: {meta.get('intentos', 0)} · contexto: {meta.get('contexto_usado', 0)} "
            f"turno(s) previo(s) · modelo: {meta.get('modelo')}"
            + (f" · redacción: {meta['origen_respuesta']}" if meta.get("origen_respuesta") else "")
        )
        if meta.get("filas") is not None:
            st.markdown("**Datos**")
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
        # Fase 27: la bitácora del chat (en disco) y el revisor de la sentencia SQL.
        bitacora=bitacora_chat.Bitacora(), revisar_sql=config.get("revisar_sql", True),
    )
    return dataclasses.replace(en_curso["res"])


def responder_en_vivo(pregunta: str) -> None:
    historial = st.session_state["historial"]
    contexto = contexto_de_seguimiento(historial)
    config = st.session_state.get(CLAVE_CONFIG_EN_CURSO) or config_actual()
    tarjeta = st.container(border=True)
    with tarjeta.chat_message("user"):
        st.markdown(pregunta)
    with tarjeta.chat_message("assistant"):
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
    st.title("💬 Pregúntale a la IA (Instacart)")
    st.caption(
        f"Modelo local: `{config_actual()['modelo']}` · esfuerzo {config_actual()['esfuerzo']} "
        "(⚙️ arriba para cambiarlo). Cada respuesta sale de una consulta SQL "
        "de solo lectura sobre los datos reales; el SQL queda visible en «Detalles»."
    )
    # Fase 27 (UX): cada pregunta con su respuesta en una tarjeta propia.
    historial = st.session_state["historial"]
    for k in range(0, len(historial), 2):
        with st.container(border=True):
            for mensaje in historial[k:k + 2]:
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
    """Fase 27 (UX): título y selector, el panel de la necesidad (el contenido)
    y, separadas por un divisor, las herramientas de evaluación de la IA."""
    necesidad = dashboard.render_controles(st.container(), horizontal=True)
    st.divider()
    dashboard.render_panel(necesidad)
    st.divider()
    st.subheader("🧰 Evaluación de la IA")
    st.caption("Herramientas para medir a la IA local contra la referencia del software; no cambian el panel de arriba.")
    render_iteraciones(necesidad)
    render_entrega_ia()


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
    with st.expander(f"📋 Entrega: JSON de la IA para las {len(formato_entrega.NECESIDADES_ENTREGA)} necesidades",
                     expanded=bool(entregas)):
        st.info(AVISO_ENTREGA_IA, icon="ℹ️")
        config = config_actual()
        st.caption(f"Modelo: `{config['modelo']}` · esfuerzo {config['esfuerzo']} (⚙️ arriba para cambiarlo). "
                   "Cada JSON es UNA corrida aparte a temperatura 0.0: no es ninguno de los intentos de "
                   "«🧪 Iteraciones con juez IA».")
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
# Fase 11: iteraciones configurables + juez IA contra la respuesta correcta
# ---------------------------------------------------------------------------

#: {necesidad: ij.CorridaIteraciones} de la última corrida de cada necesidad.
#: Se guarda ANTES de empezar y cada intento se agrega apenas termina
#: (Fase 23): si la ejecución se interrumpe, no se pierde ningún intento terminado.
CLAVE_CORRIDAS = "corridas_iteraciones"
CLAVE_MODO_ITERACION = "modo_iteracion_sel"
ETIQUETAS_MODO = {
    ij.MODO_AUTOCORRECCION: "Autocorrección encadenada",
    ij.MODO_INDEPENDIENTE: "Independientes",
}
AVISO_ITERACIONES = (
    "Corre la pregunta fija de esta necesidad **N veces con cada modelo** (pequeño y grande), por el "
    "mismo pipeline del chat (SQL de solo lectura, guardas, validación de cifras), con temperatura "
    f"{ij.TEMPERATURA_ITERACIONES}. En **autocorrección**, el veredicto lo decide un **chequeo automático** "
    "que compara el resultado del intento con el de la referencia del software determinista "
    "(`data/referencia/{nid}.json`); el mismo modelo, como **juez** (temperatura "
    f"{ij.TEMPERATURA_JUEZ}), solo explica por qué falló un intento fallido, y su opinión sobre cada intento "
    "se muestra aparte, sin decidir nada. En **independientes**, el juez decide (como antes). Un intento "
    "cuyo pipeline falló cuenta como incorrecto sin llamar al juez."
)
AVISO_MODO = {
    ij.MODO_AUTOCORRECCION: (
        "**Modo autocorrección:** desde el intento 2, cada intento recibe del intento anterior **del mismo "
        "modelo** su SQL, el error exacto o la forma del resultado (filas y columnas), un chequeo de forma "
        "automático, el veredicto del **chequeo automático** y, si falló, el **diagnóstico del enfoque** del "
        "juez, saneado: nunca un valor de la referencia. Mide si el modelo **converge** a la respuesta "
        "correcta cuando se le dice en qué falló. La cadena **se corta en el primer acierto**: los intentos "
        "siguientes quedan como «no ejecutados (ya convergió)»."
    ),
    ij.MODO_INDEPENDIENTE: (
        "**Modo independiente:** cada intento parte de cero (misma pregunta, sin ver los anteriores). "
        "Mide la **estabilidad/variabilidad** del modelo."
    ),
}
_ICONOS_VEREDICTO = {ij.CORRECTA: "✅", ij.PARCIAL: "🟡", ij.INCORRECTA: "❌", ij.SIN_VEREDICTO: "❔"}
#: Mismo criterio que el chat (`render_aviso_cifras`): de dónde salió el texto final.
ORIGEN_REDACCION = {
    "modelo": "🤖 redactada por el modelo",
    "reintento": "🤖 segunda redacción del modelo (la primera alteró cifras)",
    "plantilla": "🧩 plantilla sin modelo, con los valores reales (el modelo alteró cifras dos veces)",
}


def render_iteracion(it: ij.Iteracion) -> None:
    """Tarjeta de UN intento, etiquetada "Intento k de N · modelo": todo lo
    que muestra (SQL, resultado crudo, redacciones, veredicto) es de ese
    intento y de ningún otro."""
    with st.container(border=True):
        st.markdown(f"##### {_ICONOS_VEREDICTO[it.veredicto]} Intento {it.numero} de {it.total} · "
                    f"`{it.modelo}` → **{it.veredicto.capitalize()}**")
        if it.retroalimentacion:
            st.caption(f"↩️ Contexto que recibió este intento (lo que pasó en el intento {it.numero - 1} "
                       f"de {it.total}):")
            st.text(it.retroalimentacion)
        st.markdown(f"**SQL generado** (intento {it.numero} de {it.total})")
        if it.sql_rechazado:
            st.caption(f"Primer SQL de este intento, rechazado por: {it.motivo_reintento}")
            st.code(it.sql_rechazado, language="sql")
            st.caption("SQL del reintento, el que se ejecutó:")
        if it.sql:
            st.code(it.sql, language="sql")
        else:
            st.caption("No se llegó a generar SQL.")
        if it.ok:
            mostradas = len(it.filas or [])
            st.markdown(f"**Resultado crudo de la consulta** (intento {it.numero} de {it.total}: "
                        f"{it.n_filas} fila(s)" + (f", se muestran las primeras {mostradas}" if mostradas < (it.n_filas or 0) else "")
                        + ")")
            if it.columnas:
                st.dataframe(pd.DataFrame(it.filas or [], columns=it.columnas), use_container_width=True)
            st.markdown(f"**Redacción final** (intento {it.numero} de {it.total} · "
                        f"{ORIGEN_REDACCION.get(it.origen_respuesta, it.origen_respuesta)}): {it.respuesta}")
            if it.origen_respuesta in ("reintento", "plantilla"):
                render_aviso_cifras({"origen_respuesta": it.origen_respuesta,
                                     "cifras_rechazadas": it.cifras_rechazadas})
                st.caption(f"Primera redacción del modelo en este intento (descartada): {it.primera_redaccion}")
                if it.origen_respuesta == "plantilla" and it.segunda_redaccion:
                    st.caption(f"Segunda redacción (también descartada): {it.segunda_redaccion}")
        else:
            st.error(it.error, icon="⚠️")
        if it.chequeo_forma:
            st.markdown(f"**Chequeo automático de forma** (intento {it.numero} de {it.total}): "
                        + " ".join(it.chequeo_forma))
        if it.arbitro == ij.ARBITRO_CHEQUEO:
            st.markdown(f"**Veredicto — chequeo automático contra la referencia** (intento {it.numero} de "
                        f"{it.total}): {_ICONOS_VEREDICTO[it.veredicto]} {it.veredicto} — {it.justificacion}")
            st.caption("☝️ El detalle puede citar la referencia: al modelo solo le llega el veredicto.")
            if it.juez_veredicto is not None:
                st.caption(f"⚖️ Opinión del juez-LLM (solo comparación, no decide nada): "
                           f"{_ICONOS_VEREDICTO[it.juez_veredicto]} {it.juez_veredicto} — {it.juez_justificacion}")
        else:
            st.markdown(f"**Juez** (intento {it.numero} de {it.total}): {_ICONOS_VEREDICTO[it.veredicto]} "
                        f"{it.veredicto} — {it.justificacion}")
            if it.origen_veredicto == ij.ORIGEN_JUEZ:
                st.caption("☝️ Comparación con la referencia: puede citarla, por eso NUNCA se pasa al modelo.")
        if it.diagnostico is not None:
            st.markdown(f"**Diagnóstico del enfoque** (lo que recibe el intento siguiente): "
                        f"{it.diagnostico_retro or '(vacío)'}")
            if it.oraciones_omitidas:
                st.caption(f"Se omitieron {it.oraciones_omitidas} oración(es) por citar valores de la referencia. "
                           f"Diagnóstico crudo del juez: {it.diagnostico}")
        if it.coincide_referencia is not None and it.arbitro != ij.ARBITRO_CHEQUEO:
            st.caption(("✅ Coincide" if it.coincide_referencia else "✖️ No coincide")
                       + f" con la referencia según el chequeo automático ({it.detalle_coincidencia}); "
                       "es informativo, no se pasa al modelo.")
        st.caption(
            f"Intentos de SQL: {it.intentos_sql} · veredicto: {it.origen_veredicto} · "
            f"pipeline {it.duracion_pipeline_s:.1f} s · juez {it.duracion_juez_s:.1f} s"
        )


def render_resumen_iteraciones(corrida: ij.CorridaIteraciones) -> None:
    resumen = corrida.resumen()
    columnas = st.columns(len(resumen))
    for col, (etiqueta, r) in zip(columnas, zip(("Pequeño", "Grande"), resumen.values())):
        arbitro = "chequeo automático" if corrida.arbitro == ij.ARBITRO_CHEQUEO else "juez-LLM"
        if corrida.corta_al_acertar:
            # Fase 26: con corte al primer acierto, lo que cuenta es en qué estado termina la cadena.
            conv = corrida.convergencia.get(r.modelo)
            col.metric(f"{etiqueta}: {r.modelo}",
                       f"✅ Convergió en el intento {conv}" if conv else
                       f"❌ No convergió ({r.n} de {corrida.n_iteraciones} intentos)",
                       help=f"La cadena se corta en el primer intento «correcta» (árbitro: {arbitro}); "
                            "los intentos siguientes no se ejecutan.")
        else:
            col.metric(f"{etiqueta}: {r.modelo}", f"{r.pct_correctas:.0f}% correctas",
                       help=f"Iteraciones con veredicto «correcta» sobre el total de iteraciones del modelo "
                            f"(árbitro: {arbitro}).")
        col.caption(f"{r.correctas} correctas · {r.parciales} parciales · {r.incorrectas} incorrectas · "
                    f"{r.sin_veredicto} sin veredicto (de {r.n} ejecutadas de {corrida.n_iteraciones}"
                    + (f"; {r.no_ejecutadas} no ejecutadas porque ya convergió" if r.no_ejecutadas else "") + ")")
        col.caption(("Primer «correcta»: intento " + str(r.primer_correcto)) if r.primer_correcto
                    else "Ningún intento «correcta».")
        col.caption(f"Chequeo automático: {r.coincidencias} de {r.n} coinciden con la referencia"
                    + (f" (la primera, en el intento {r.primera_coincidencia})" if r.primera_coincidencia else "")
                    + ".")
        col.caption("Secuencia: " + " → ".join(
            [f"{it.numero}{_ICONOS_VEREDICTO[it.veredicto]}" for it in corrida.de_modelo(r.modelo)]
            + [f"{k}⏭️" for k in corrida.no_ejecutadas(r.modelo)]))
        if corrida.arbitro == ij.ARBITRO_CHEQUEO and r.juez_opiniones:
            col.caption(f"⚖️ Juez-LLM (solo comparación): {r.juez_correctas} de {r.juez_opiniones} «correcta»; "
                        f"de acuerdo con el chequeo en {r.juez_acuerdos} de {r.juez_opiniones} "
                        f"({r.juez_falsos_negativos} correctos que el juez no dio por correctos, "
                        f"{r.juez_falsos_positivos} incorrectos que dio por correctos).")


def _referencia_texto(necesidad: str, corrida: ij.CorridaIteraciones | None) -> str | None:
    if corrida is not None:
        return corrida.referencia
    try:
        return ij.referencia_de(necesidad)
    except Exception:  # noqa: BLE001 - el panel de la necesidad ya muestra el error de carga
        return None


def render_exportar_todas(corridas: dict) -> None:
    if not corridas:
        return
    orden = sorted(corridas.values(), key=lambda c: int(c.necesidad[1:]))
    nids = ", ".join(c.necesidad for c in orden)
    st.caption(f"Exportar el log de **todas** las necesidades corridas en esta sesión ({nids}), para "
               "pegarlo en otra conversación:")
    c1, c2 = st.columns(2)
    c1.download_button(f"⬇️ {ij.NOMBRE_EXPORT_TODAS_TEXTO}", data=ij.exportar_texto(orden).encode("utf-8"),
                       file_name=ij.NOMBRE_EXPORT_TODAS_TEXTO, mime="text/plain", key="exportar_todas_txt")
    c2.download_button(f"⬇️ {ij.NOMBRE_EXPORT_TODAS_JSON}", data=ij.exportar_json(orden).encode("utf-8"),
                       file_name=ij.NOMBRE_EXPORT_TODAS_JSON, mime="application/json", key="exportar_todas_json")


def render_iteraciones(necesidad: str) -> None:
    corridas = st.session_state.setdefault(CLAVE_CORRIDAS, {})
    corrida = corridas.get(necesidad)
    en_curso = bool(st.session_state.get("pregunta_en_curso"))
    with st.expander(f"🧪 Iteraciones con juez IA: modelo pequeño vs grande ({necesidad})",
                     expanded=corrida is not None):
        st.info(AVISO_ITERACIONES.replace("{nid}", necesidad), icon="ℹ️")
        cfg = config_iteraciones()
        st.markdown(AVISO_MODO[cfg["modo"]])
        modelos = list(dict.fromkeys([cfg["modelo_pequeno"], cfg["modelo_grande"]]))
        st.markdown(f"**Pregunta ({necesidad}):** {ij.PREGUNTAS[necesidad]}")
        referencia = _referencia_texto(necesidad, corrida)
        if referencia is not None:
            with st.container(border=True):
                st.markdown(f"**Respuesta de referencia (correcta)** — SQL, resultado, redacción y valores clave "
                            f"de `data/referencia/{necesidad}.json`; es lo que ve el juez (nunca el modelo):")
                st.text(referencia)
        st.caption(f"{cfg['iteraciones']} iteraciones × {len(modelos)} modelo(s): `{'`, `'.join(modelos)}` · "
                   f"modo {ETIQUETAS_MODO[cfg['modo']].lower()} · esfuerzo {cfg['esfuerzo']} (⚙️ arriba para cambiarlo).")
        if len(modelos) == 1:
            st.warning("El modelo pequeño y el grande son el mismo: se corre una sola vez.")
        if st.button(f"▶️ Correr {cfg['iteraciones']} iteraciones por modelo", key=f"iterar_{necesidad}",
                     disabled=en_curso,
                     help="Deshabilitado mientras el chat responde una pregunta." if en_curso else None):
            barra = st.progress(0.0, text="Cargando la respuesta de referencia…")
            vivo = st.empty()  # intentos terminados, en vivo, mientras corre el resto

            def _inicio(modelo: str, numero: int, hechas: int, total: int) -> None:
                barra.progress(hechas / total, text=f"Corriendo {modelo} · intento {numero} de "
                                                    f"{cfg['iteraciones']} ({hechas} de {total} terminados)…")

            def _avance(it: ij.Iteracion, hechas: int, total: int) -> None:
                barra.progress(hechas / total, text=f"{hechas} de {total} terminados · último: "
                                                    f"{it.etiqueta} → {it.veredicto}")
                with vivo.container():
                    for terminado in corrida.iteraciones:
                        render_iteracion(terminado)

            try:
                corrida = ij.nueva_corrida(necesidad, modelos=modelos, n=cfg["iteraciones"], modo=cfg["modo"])
                corridas[necesidad] = corrida
                ij.correr_iteraciones(
                    necesidad, modelos=modelos, corrida=corrida,
                    timeout_sql_llm_s=cfg["timeout_sql_s"], timeout_respuesta_llm_s=cfg["timeout_respuesta_s"],
                    al_iniciar=_inicio, al_avanzar=_avance,
                )
            except Exception as exc:  # noqa: BLE001 - p. ej. no se pudo cargar la referencia
                st.error(f"No se pudo correr la prueba de iteraciones: {exc}", icon="⚠️")
            barra.empty()
            vivo.empty()  # las tarjetas en vivo se reemplazan por el panel completo de abajo
        if corrida is None:
            render_exportar_todas(corridas)
            return

        st.markdown(f"**Resultado** · {corrida.timestamp} · modo {ETIQUETAS_MODO[corrida.modo].lower()} · "
                    f"temperatura {corrida.temperatura} (juez {corrida.temperatura_juez}) · "
                    f"{len(corrida.iteraciones)} de {corrida.total_esperado} intentos ejecutados"
                    + (f", {corrida.total_no_ejecutadas} no ejecutados porque ya había convergido"
                       if corrida.total_no_ejecutadas else ""))
        if not corrida.completa:
            st.warning(f"Corrida interrumpida: se registraron {len(corrida.iteraciones)} de "
                       f"{corrida.total_esperado} intentos. Se muestran todos los que alcanzaron a terminar; "
                       "los que faltan no llegaron a correr completos.", icon="⏸️")
        render_resumen_iteraciones(corrida)
        for pestana, modelo in zip(st.tabs(corrida.modelos), corrida.modelos):
            with pestana:
                for it in corrida.de_modelo(modelo):
                    render_iteracion(it)
                for k in corrida.no_ejecutadas(modelo):
                    st.info(f"⏭️ Intento {k} de {corrida.n_iteraciones} · `{modelo}` → "
                            f"{ij.texto_no_ejecutada(corrida.convergencia[modelo])}: no falló, no se corrió.")
        c1, c2 = st.columns(2)
        c1.download_button(
            f"⬇️ {ij.nombre_archivo_texto(necesidad)} (log legible)", data=ij.a_texto(corrida).encode("utf-8"),
            file_name=ij.nombre_archivo_texto(necesidad), mime="text/plain", key=f"descargar_iter_txt_{necesidad}",
        )
        c2.download_button(
            f"⬇️ Descargar {ij.nombre_archivo(necesidad)}", data=ij.a_json(corrida).encode("utf-8"),
            file_name=ij.nombre_archivo(necesidad), mime="application/json", key=f"descargar_iter_{necesidad}",
        )
        render_exportar_todas(corridas)


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
               "El timeout de la consulta en DuckDB (10 s) es una guarda y no cambia con el esfuerzo "
               "(en el panel de iteraciones, N9 usa 25 s: su self-join es pesado).")

    st.divider()
    render_config_iteraciones(modelos)

    st.divider()
    render_config_bitacora()

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


def _borrar_bitacora() -> None:
    bitacora_chat.Bitacora().borrar()


def render_config_bitacora() -> None:
    """Fase 27: bitácora de aprendizaje del chat (en disco) y revisor de SQL."""
    st.markdown("**Aprendizaje del chat**")
    st.checkbox("Revisar cada SQL del chat con el modelo (juez de la sentencia)", value=True, key=CLAVE_REVISAR_SQL,
                help="Una llamada más por pregunta: el modelo revisa si la SENTENCIA tiene sentido para la "
                     "pregunta y usa columnas y JOINs reales (no si la respuesta es correcta: no hay referencia). "
                     "Si la marca sospechosa, se reintenta una vez.")
    bitacora = bitacora_chat.Bitacora()
    lecciones = bitacora.cargar()
    en_prompt = {x.firma for x in bitacora.para_prompt()}
    st.caption(f"📓 Bitácora: {len(lecciones)} lección(es) guardada(s) en `{bitacora.ruta.as_posix()}`; "
               f"{len(en_prompt)} van en el prompt de cada pregunta (máximo {bitacora_chat.MAX_LECCIONES_PROMPT}).")
    for x in bitacora_chat.por_prioridad(lecciones):
        marca = "📌" if x.firma in en_prompt else "·"
        st.caption(f"{marca} {x.leccion} (×{x.veces})")
    en_curso = bool(st.session_state.get("pregunta_en_curso"))
    st.button("🗑️ Borrar la bitácora", key="borrar_bitacora", use_container_width=True,
              disabled=not lecciones or en_curso, on_click=_borrar_bitacora)


def _elegir_por_defecto(clave: str, preferido: str, modelos: list[str]) -> None:
    if st.session_state.get(clave) not in modelos:
        st.session_state[clave] = preferido if preferido in modelos else modelos[0]


def render_config_iteraciones(modelos: list[str] | None) -> None:
    """Sección «Prueba de iteraciones» del popover ⚙️ (Fase 11)."""
    st.markdown("**Prueba de iteraciones (juez IA)**")
    st.number_input(
        "Cantidad de iteraciones", min_value=ij.ITERACIONES_MIN, max_value=ij.ITERACIONES_MAX, step=1,
        key=CLAVE_ITERACIONES,
        help="Cuántas veces se corre la pregunta completa con CADA modelo (pequeño y grande) en "
             "«🧪 Iteraciones con juez IA» del dashboard. No afecta al chat.",
    )
    if modelos:
        _elegir_por_defecto(CLAVE_MODELO_PEQUENO, ij.MODELO_PEQUENO_DEFAULT, modelos)
        _elegir_por_defecto(CLAVE_MODELO_GRANDE, ij.MODELO_GRANDE_DEFAULT, modelos)
        st.selectbox("Modelo pequeño", modelos, key=CLAVE_MODELO_PEQUENO)
        st.selectbox("Modelo grande", modelos, key=CLAVE_MODELO_GRANDE)
    st.selectbox(
        "Modo de iteración", list(ETIQUETAS_MODO), format_func=ETIQUETAS_MODO.get, key=CLAVE_MODO_ITERACION,
        help="Autocorrección: desde el intento 2, cada intento ve el SQL, el resultado y el veredicto del "
             "anterior (mide convergencia). Independientes: cada intento desde cero (mide variabilidad).",
    )
    st.caption(f"Temperatura: {ij.TEMPERATURA_ITERACIONES} en las iteraciones (con 0.0 salen casi idénticas) "
               f"y {ij.TEMPERATURA_JUEZ} en el juez. El chat sigue en 0.0. Timeouts: los del esfuerzo.")


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
    estilos.aplicar()
    render_fijados()
    aviso = render_barra_vistas()
    if st.session_state["vista"] == VISTA_DASHBOARD:
        render_dashboard()
    else:
        render_chat()
        aviso.empty()


if __name__ == "__main__":
    main()
