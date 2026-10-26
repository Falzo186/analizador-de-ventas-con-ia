"""Fase 31: línea de tiempo por intento (botones de las 10 necesidades y chat libre).

Cada intento es un contenedor con borde (separación visual de la Fase 27) con:
encabezado y tiempos, pregunta y pista recibida, SQL, resultado o error,
respuesta, evaluación con su «por qué», opinión del juez/revisor (que no
decide) y la pista que viaja al intento siguiente.

Solo muestra datos que ya produjeron el pipeline y el motor de iteraciones:
no llama al modelo ni recalcula veredictos.

- Con referencia (botón): el veredicto es el del chequeo automático (Fase 25)
  y el «por qué» es `Iteracion.explicacion_usuario` (puede citar la
  referencia: es para la pantalla). Lo que viajó al siguiente intento es
  `Iteracion.pista_modelo` (saneado).
- Sin referencia (chat libre): solo «SQL sano / sospechoso / error de
  ejecución», con el motivo del revisor. Nunca «correcta».
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.llm import iteraciones_juez as ij
from src.llm import retroalimentacion as rf

EVALUACION_CON_REFERENCIA = {
    ij.CORRECTA: ("✅", "Correcta"),
    ij.PARCIAL: ("🟡", "Parcialmente correcta"),
    ij.INCORRECTA: ("❌", "Incorrecta"),
    ij.SIN_VEREDICTO: ("❔", "Sin veredicto"),
}
SQL_SANO = ("✅", "SQL sano")
SQL_SOSPECHOSO = ("⚠️", "SQL sospechoso")
ERROR_EJECUCION = ("❌", "Error de ejecución")
RECHAZO_GUARDA = ("🛡️", "Rechazado por la guarda")
SIN_REVISION = ("ℹ️", "Sin revisión del SQL")
REINTENTO_REVISOR = ("ℹ️", "Reintento pedido por el revisor (este SQL ya no se vuelve a revisar)")
AVISO_SIN_REFERENCIA = ("Sin respuesta de referencia: solo se evalúa si el SQL es sano, no si la respuesta es la "
                        "correcta.")
SIN_PISTA_CONVERGE = "sin pista: ya converge"
SIN_PISTA_FIN = "sin pista: no hay más intentos"
FASES = (("generacion_sql_s", "generación de SQL"), ("ejecucion_sql_s", "ejecución"), ("revisor_s", "revisor"),
         ("redaccion_s", "redacción"))
FILAS_MOSTRADAS = 10


def texto_tiempos(tiempos: dict | None) -> str:
    partes = [f"{nombre} {tiempos[k]:.1f} s" for k, nombre in FASES if tiempos and k in tiempos]
    return " · ".join(partes)


# ---------------------------------------------------------------------------
# Vistas (dicts) -- testeables sin Streamlit
# ---------------------------------------------------------------------------

def vista_iteracion(it: ij.Iteracion, pregunta: str) -> dict:
    icono, etiqueta = EVALUACION_CON_REFERENCIA.get(it.veredicto, ("❔", it.veredicto))
    juez = None
    if it.juez_veredicto is not None or it.diagnostico:
        partes = []
        if it.juez_veredicto is not None:
            partes.append(f"veredicto del juez: {it.juez_veredicto} — {it.juez_justificacion or ''}".strip(" —"))
        if it.diagnostico:
            partes.append(f"diagnóstico del enfoque: {it.diagnostico}")
        discrepa = it.juez_veredicto is not None and (it.juez_veredicto == ij.CORRECTA) != (it.veredicto == ij.CORRECTA)
        juez = {"texto": " · ".join(partes), "discrepa": discrepa}
    if it.pista_modelo:
        siguiente = it.pista_modelo
    else:
        siguiente = SIN_PISTA_CONVERGE if it.veredicto == ij.CORRECTA else SIN_PISTA_FIN
    return {
        "titulo": f"Intento {it.numero} de {it.total} · {it.modelo} · {it.tecnica}",
        "tiempo_s": it.duracion_pipeline_s, "tiempos": it.tiempos,
        "evaluador": "juez", "tiempo_evaluador_s": it.duracion_juez_total_s,
        "pregunta": pregunta, "pista_recibida": it.retroalimentacion,
        "sql_rechazado": it.sql_rechazado, "motivo_reintento": it.motivo_reintento, "sql": it.sql,
        "columnas": it.columnas, "filas": it.filas, "n_filas": it.n_filas, "error": it.error,
        "respuesta": it.respuesta,
        "evaluacion": {"icono": icono, "etiqueta": etiqueta,
                       "porque": it.explicacion_usuario or it.justificacion},
        "juez": juez, "pista_siguiente": siguiente,
    }


def _texto_reintento(sql: str | None, motivo: str | None) -> str:
    """Lo que viaja al reintento del chat (`prompt_sql_reintento_instacart`)."""
    return (f"Tu consulta anterior fue:\n{(sql or '').strip()}\n\n"
            f"Tu consulta anterior fue rechazada porque: {(motivo or '').strip()}")


def evaluacion_chat(meta: dict) -> dict:
    """✅ SQL sano / ⚠️ SQL sospechoso / ❌ Error de ejecución (nunca «correcta»)."""
    rev = meta.get("revision_sql") or {}
    if not meta.get("ok"):
        icono, etiqueta = RECHAZO_GUARDA if meta.get("tipo_error") == "rechazo" else ERROR_EJECUCION
        return {"icono": icono, "etiqueta": etiqueta, "porque": meta.get("error") or meta.get("contenido") or ""}
    if not rev:
        return {"icono": SIN_REVISION[0], "etiqueta": SIN_REVISION[1],
                "porque": "El revisor de SQL está desactivado (⚙️)."}
    if rev.get("sospechoso"):
        porque = f"El revisor la marcó: {rev.get('motivo', '')}"
        if meta.get("reintento_por_revision"):
            porque += (" Se reintentó; " + ("el resultado CAMBIÓ." if rev.get("resultado_cambio") else
                                             "el resultado fue IDÉNTICO: la alarma no tuvo efecto."))
        elif rev.get("reintento_error"):
            porque += f" El reintento no ejecutó ({rev['reintento_error']}); se conservó el resultado original."
        return {"icono": SQL_SOSPECHOSO[0], "etiqueta": SQL_SOSPECHOSO[1], "porque": porque}
    return {"icono": SQL_SANO[0], "etiqueta": SQL_SANO[1],
            "porque": f"El revisor no señaló problemas: {rev.get('motivo') or 'la sentencia usa columnas y JOINs reales'}."}


def vistas_chat(mensaje: dict) -> list[dict]:
    """Los intentos de SQL de una pregunta libre (1, o 2 si hubo reintento)."""
    meta = mensaje["metadatos"]
    final = evaluacion_chat(meta)
    rev = meta.get("revision_sql") or {}
    base = {"evaluador": "revisor", "pregunta": meta.get("pregunta"),
            "titulo_sufijo": f"{meta.get('modelo')} · {meta.get('tecnica', 'base')}"}
    vistas = []
    total = 2 if meta.get("sql_rechazado") else 1
    if meta.get("sql_rechazado"):
        motivo = meta.get("motivo_reintento")
        if meta.get("reintento_por_revision"):
            ev = {"icono": SQL_SOSPECHOSO[0], "etiqueta": SQL_SOSPECHOSO[1], "porque": motivo}
        elif meta.get("motivo_reintento_es_ejecucion"):
            ev = {"icono": ERROR_EJECUCION[0], "etiqueta": ERROR_EJECUCION[1], "porque": motivo}
        else:
            ev = {"icono": RECHAZO_GUARDA[0], "etiqueta": RECHAZO_GUARDA[1], "porque": motivo}
        vistas.append({**base, "titulo": f"Intento 1 de {total} · {base['titulo_sufijo']}", "tiempo_s": None,
                       "tiempos": None, "tiempo_evaluador_s": None, "pista_recibida": None,
                       "sql": meta["sql_rechazado"], "columnas": meta.get("columnas_antes_revision"),
                       "filas": meta.get("filas_antes_revision"), "n_filas": None,
                       "error": None if meta.get("reintento_por_revision") else motivo, "respuesta": None,
                       "evaluacion": ev, "juez": None,
                       "pista_siguiente": _texto_reintento(meta["sql_rechazado"], motivo)})
    tiempos = meta.get("tiempos") or {}
    juez = None
    if rev:
        juez = {"texto": f"{'sospechoso' if rev.get('sospechoso') else 'sin problemas'} — {rev.get('motivo', '')}",
                "discrepa": False}
    ultimo_ev = (final if not meta.get("reintento_por_revision") or not meta.get("ok")
                 else {"icono": REINTENTO_REVISOR[0], "etiqueta": REINTENTO_REVISOR[1], "porque": final["porque"]})
    vistas.append({**base, "titulo": f"Intento {total} de {total} · {base['titulo_sufijo']}",
                   "tiempo_s": round(sum(v for k, v in tiempos.items()), 2) if tiempos else None,
                   "tiempos": tiempos, "tiempo_evaluador_s": tiempos.get("revisor_s"),
                   "pista_recibida": (_texto_reintento(meta.get("sql_rechazado"), meta.get("motivo_reintento"))
                                      if meta.get("sql_rechazado") else None),
                   "sql": mensaje.get("sql"), "columnas": meta.get("columnas"), "filas": meta.get("filas"),
                   "n_filas": meta.get("n_filas"), "error": None if meta.get("ok") else mensaje.get("contenido"),
                   "respuesta": mensaje.get("contenido") if meta.get("ok") else None,
                   "evaluacion": ultimo_ev, "juez": juez,
                   "pista_siguiente": SIN_PISTA_CONVERGE if meta.get("ok") else SIN_PISTA_FIN})
    return vistas


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_intento(v: dict) -> None:
    with st.container(border=True):
        cabeza = f"**{v['evaluacion']['icono']} {v['titulo']}**"
        tiempos = []
        if v.get("tiempo_s") is not None:
            tiempos.append(f"intento {v['tiempo_s']:.1f} s")
        if v.get("tiempo_evaluador_s") is not None:
            tiempos.append(f"{v['evaluador']} {v['tiempo_evaluador_s']:.1f} s")
        st.markdown(cabeza + (f" · ⏱️ {' · '.join(tiempos)}" if tiempos else ""))
        if texto_tiempos(v.get("tiempos")):
            st.caption(f"Por fase: {texto_tiempos(v.get('tiempos'))}")
        st.markdown(f"**Pregunta enviada:** {v.get('pregunta')}")
        if v.get("pista_recibida"):
            st.markdown("**Pista que recibió del intento anterior** (texto exacto que viajó al prompt):")
            st.code(v["pista_recibida"], language="text")
        if v.get("sql_rechazado"):
            st.caption(f"Primer SQL de este intento, rechazado por: {v.get('motivo_reintento')}")
            st.code(v["sql_rechazado"], language="sql")
        st.markdown("**SQL generado**")
        if v.get("sql"):
            st.code(v["sql"], language="sql")
        else:
            st.caption("No se llegó a generar SQL.")
        if v.get("error"):
            st.markdown("**Error exacto**")
            st.code(v["error"], language="text")
        elif v.get("columnas"):
            n = v.get("n_filas") if v.get("n_filas") is not None else len(v.get("filas") or [])
            st.markdown(f"**Resultado crudo** ({n} fila(s); se muestran hasta {FILAS_MOSTRADAS})")
            st.dataframe(pd.DataFrame((v.get("filas") or [])[:FILAS_MOSTRADAS], columns=v["columnas"]),
                         use_container_width=True, hide_index=True)
        if v.get("respuesta"):
            st.markdown(f"**Respuesta de la IA:** {v['respuesta']}")
        ev = v["evaluacion"]
        st.markdown(f"**Evaluación:** {ev['icono']} {ev['etiqueta']}")
        if ev.get("porque"):
            st.caption(f"Por qué: {ev['porque']}")
        if v.get("juez"):
            rotulo = "Revisor" if v.get("evaluador") == "revisor" else "Juez-LLM"
            st.caption(f"🧑‍⚖️ {rotulo} — diagnóstico (no decide el veredicto): {v['juez']['texto']}")
            if v["juez"].get("discrepa"):
                st.warning("El juez no coincide con el chequeo automático.", icon="⚖️")
        st.markdown("**Pista para el siguiente intento:**")
        if v["pista_siguiente"] in (SIN_PISTA_CONVERGE, SIN_PISTA_FIN):
            st.caption(v["pista_siguiente"])
        else:
            st.code(v["pista_siguiente"], language="text")


def render_cierre(corrida: ij.CorridaIteraciones, modelo: str) -> None:
    c = ij.cierre_de(corrida, modelo)
    (st.success if c.aceptada_en else st.error)(f"**{c.texto}.** {c.explicacion}",
                                                icon="✅" if c.aceptada_en else "❌")
    st.caption(f"Tiempo total del modelo: {c.tiempo_modelo_s:.1f} s · tiempo total del juez: {c.tiempo_juez_s:.1f} s"
               + (" · iteración cortada al primer acierto" if c.corte else ""))
    st.caption(rf.REGLA_PARCIAL)


def render_corrida(corrida: ij.CorridaIteraciones) -> None:
    """Línea de tiempo expandida de una corrida de botón y su cierre."""
    for modelo in corrida.modelos:
        for it in corrida.de_modelo(modelo):
            render_intento(vista_iteracion(it, corrida.pregunta))
        for k in corrida.no_ejecutadas(modelo):
            st.caption(f"⏭️ Intento {k} de {corrida.n_iteraciones}: {ij.texto_no_ejecutada(corrida.convergencia[modelo])}")
        render_cierre(corrida, modelo)
